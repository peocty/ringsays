"""Append only, hash chained audit log.

Each event hash covers its own fields plus previous event hash for same tenant, so any edit or deletion
breaks the chain. Database trigger also rejects UPDATE and DELETE. A per tenant advisory lock serialises
appends so the chain never forks.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import Connection, func, insert, select, text

from app.core.ids import uuid7
from app.core.tables import audit_events

GENESIS = "0" * 64


@dataclass(frozen=True, slots=True)
class AuditRecord:
    event_id: UUID
    tenant_id: UUID | None
    at: datetime
    actor: str
    action: str
    object_type: str
    object_id: str
    reason: str | None
    prev_hash: str
    hash: str


def compute_hash(
    prev_hash: str,
    event_id: UUID,
    tenant_id: UUID | None,
    at: datetime,
    actor: str,
    action: str,
    object_type: str,
    object_id: str,
    reason: str | None,
) -> str:
    body = json.dumps(
        {
            "prev": prev_hash,
            "id": str(event_id),
            "tenant": str(tenant_id) if tenant_id else None,
            "at": at.astimezone(UTC).isoformat(timespec="microseconds"),
            "actor": actor,
            "action": action,
            "type": object_type,
            "object": object_id,
            "reason": reason,
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(body.encode()).hexdigest()


def append(
    conn: Connection,
    *,
    tenant_id: UUID | None,
    actor: str,
    action: str,
    object_type: str,
    object_id: str,
    at: datetime,
    reason: str | None = None,
) -> AuditRecord:
    lock_key = str(tenant_id) if tenant_id else "consumer"
    conn.execute(text("SELECT pg_advisory_xact_lock(hashtext(:k))"), {"k": f"audit:{lock_key}"})
    prev = (
        conn.execute(
            select(audit_events.c.hash)
            .where(audit_events.c.tenant_id == tenant_id if tenant_id else audit_events.c.tenant_id.is_(None))
            .order_by(audit_events.c.id.desc())
            .limit(1)
        ).scalar_one_or_none()
        or GENESIS
    )
    event_id = uuid7()
    digest = compute_hash(prev, event_id, tenant_id, at, actor, action, object_type, object_id, reason)
    record = AuditRecord(event_id, tenant_id, at, actor, action, object_type, object_id, reason, prev, digest)
    conn.execute(
        insert(audit_events).values(
            event_id=event_id,
            tenant_id=tenant_id,
            at=at,
            actor=actor,
            action=action,
            object_type=object_type,
            object_id=object_id,
            reason=reason,
            prev_hash=prev,
            hash=digest,
        )
    )
    return record


def verify_chain(conn: Connection, tenant_id: UUID, anchored_head: str | None = None) -> tuple[bool, int]:
    """Recompute whole chain for a tenant. Returns (intact, events checked).

    A chain alone cannot prove nothing was removed from its end. Pass `anchored_head`, a hash previously
    exported by `chain_heads` to write once storage; verification then also fails if that hash is gone.
    """
    rows = conn.execute(
        select(audit_events).where(audit_events.c.tenant_id == tenant_id).order_by(audit_events.c.id)
    ).all()
    prev = GENESIS
    for r in rows:
        expected = compute_hash(
            prev, r.event_id, r.tenant_id, r.at, r.actor, r.action, r.object_type, r.object_id, r.reason
        )
        if r.prev_hash != prev or r.hash != expected:
            return False, len(rows)
        prev = r.hash
    if anchored_head is not None and anchored_head not in {r.hash for r in rows}:
        return False, len(rows)
    return True, len(rows)


def chain_heads(conn: Connection) -> dict[str, str]:
    """Latest hash per tenant, for export to write once storage by an operations job.

    Must run under worker role. Export target (for example object storage with retention lock) is an
    operations integration not built here.
    """
    rows = conn.execute(
        text(
            "SELECT DISTINCT ON (tenant_id) coalesce(tenant_id::text, 'consumer') AS tenant_key, hash "
            "FROM audit.audit_events ORDER BY tenant_id, id DESC"
        )
    ).all()
    return {r.tenant_key: r.hash for r in rows}


def count(conn: Connection, tenant_id: UUID) -> int:
    return int(
        conn.execute(
            select(func.count()).select_from(audit_events).where(audit_events.c.tenant_id == tenant_id)
        ).scalar_one()
    )
