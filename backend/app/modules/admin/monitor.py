"""Intent monitor and audit log for the portal. Recipient numbers are masked; phone search uses the keyed
hash, so the full number is never selected, logged or returned."""

from __future__ import annotations

import csv
import io
from collections.abc import Iterator
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from sqlalchemy import Connection, func, select

from app.core.db import tenant_tx
from app.core.phone import phone_hash
from app.core.tables import audit_events, delivery_attempts, intent_events, intents, webhook_deliveries
from app.modules.audit import service as audit

from .access import BadRequest, Member, NotFound, Perm
from .integration import delivery_out
from .org import iso, mask_phone

_LIST_COLUMNS = [
    intents.c.id,
    intents.c.status,
    intents.c.to_phone,
    intents.c.agent_id,
    intents.c.department_id,
    intents.c.purpose_code,
    intents.c.masked_reference,
    intents.c.priority,
    intents.c.verification_level,
    intents.c.channel_used,
    intents.c.valid_from,
    intents.c.valid_until,
    intents.c.scheduled_slot,
    intents.c.decline_reason,
    intents.c.outcome_code,
    intents.c.created_at,
    intents.c.updated_at,
]


def _own_only(m: Member) -> str | None:
    """Agent id the member is limited to, or None when member may see every intent."""
    if Perm.INTENTS_READ_ALL in m.permissions:
        return None
    return m.agent_id or "\x00no-agent"


def intent_out(r: Any) -> dict[str, Any]:
    return {
        "intent_id": str(r.id),
        "status": r.status,
        "to_masked": mask_phone(r.to_phone),
        "agent_id": r.agent_id,
        "department_id": str(r.department_id) if r.department_id else None,
        "purpose_code": r.purpose_code,
        "masked_reference": r.masked_reference,
        "priority": r.priority,
        "verification_level": r.verification_level,
        "channel_used": r.channel_used,
        "valid_from": iso(r.valid_from),
        "valid_until": iso(r.valid_until),
        "scheduled_slot": r.scheduled_slot,
        "decline_reason": r.decline_reason,
        "outcome_code": r.outcome_code,
        "created_at": iso(r.created_at),
        "updated_at": iso(r.updated_at),
    }


def _cursor(r: Any) -> str:
    return f"{r.created_at.isoformat()}|{r.id}"


def _parse_cursor(cursor: str) -> tuple[datetime, UUID]:
    try:
        at, ident = cursor.split("|", 1)
        return datetime.fromisoformat(at), UUID(ident)
    except ValueError as exc:
        raise BadRequest("cursor is not valid") from exc


def list_intents(
    conn: Connection,
    m: Member,
    *,
    statuses: list[str] | None,
    purpose_code: str | None,
    agent_id: str | None,
    phone: str | None,
    created_from: datetime | None,
    created_to: datetime | None,
    limit: int,
    cursor: str | None,
) -> dict[str, Any]:
    q = select(*_LIST_COLUMNS).order_by(intents.c.created_at.desc(), intents.c.id.desc()).limit(limit + 1)
    own = _own_only(m)
    if own is not None:
        q = q.where(intents.c.agent_id == own)
    if statuses:
        q = q.where(intents.c.status.in_(statuses))
    if purpose_code:
        q = q.where(intents.c.purpose_code == purpose_code)
    if agent_id:
        q = q.where(intents.c.agent_id == agent_id)
    if phone:
        q = q.where(intents.c.to_phone_hash == phone_hash(phone))
    if created_from:
        q = q.where(intents.c.created_at >= created_from)
    if created_to:
        q = q.where(intents.c.created_at < created_to)
    if cursor:
        at, ident = _parse_cursor(cursor)
        q = q.where((intents.c.created_at < at) | ((intents.c.created_at == at) & (intents.c.id < ident)))
    rows = conn.execute(q).all()
    page = rows[:limit]
    return {
        "items": [intent_out(r) for r in page],
        "next_cursor": _cursor(page[-1]) if len(rows) > limit else None,
    }


def summary(conn: Connection, m: Member, since: datetime) -> dict[str, Any]:
    base = intents.c.created_at >= since
    own = _own_only(m)
    if own is not None:
        base = base & (intents.c.agent_id == own)
    by_status = {
        r.status: r.n
        for r in conn.execute(
            select(intents.c.status, func.count().label("n")).where(base).group_by(intents.c.status)
        ).all()
    }
    by_channel = {
        (r.channel_used or "NONE"): r.n
        for r in conn.execute(
            select(intents.c.channel_used, func.count().label("n"))
            .where(base)
            .group_by(intents.c.channel_used)
        ).all()
    }
    return {
        "since": since.isoformat(),
        "total": sum(by_status.values()),
        "by_status": by_status,
        "by_channel": by_channel,
    }


def intent_detail(conn: Connection, m: Member, intent_id: UUID) -> dict[str, Any]:
    q = select(*_LIST_COLUMNS, intents.c.proposed_slots).where(intents.c.id == intent_id)
    own = _own_only(m)
    if own is not None:
        q = q.where(intents.c.agent_id == own)
    row = conn.execute(q).one_or_none()
    if row is None:
        raise NotFound("Intent not found")
    events = conn.execute(
        select(intent_events).where(intent_events.c.intent_id == intent_id).order_by(intent_events.c.id)
    ).all()
    attempts = conn.execute(
        select(delivery_attempts)
        .where(delivery_attempts.c.intent_id == intent_id)
        .order_by(delivery_attempts.c.id)
    ).all()
    hooks = conn.execute(
        select(webhook_deliveries)
        .where(webhook_deliveries.c.intent_id == intent_id)
        .order_by(webhook_deliveries.c.id)
    ).all()
    return {
        **intent_out(row),
        "proposed_slots": row.proposed_slots or [],
        "events": [
            {"at": iso(e.at), "from_status": e.from_status, "to_status": e.to_status, "actor": e.actor}
            for e in events
        ],
        "delivery_attempts": [
            {"channel": a.channel, "outcome": a.outcome, "reason": a.reason, "at": iso(a.at)}
            for a in attempts
        ],
        "webhooks": [delivery_out(h) for h in hooks],
    }


# Audit


def audit_out(r: Any) -> dict[str, Any]:
    return {
        "event_id": str(r.event_id),
        "at": iso(r.at),
        "actor": r.actor,
        "action": r.action,
        "object_type": r.object_type,
        "object_id": r.object_id,
        "reason": r.reason,
        "hash": r.hash,
        "prev_hash": r.prev_hash,
    }


def list_audit(
    conn: Connection, tenant_id: UUID, action: str | None, limit: int, cursor: str | None
) -> dict[str, Any]:
    q = (
        select(audit_events)
        .where(audit_events.c.tenant_id == tenant_id)
        .order_by(audit_events.c.id.desc())
        .limit(limit + 1)
    )
    if action:
        q = q.where(audit_events.c.action == action)
    if cursor:
        if not cursor.isdigit():
            raise BadRequest("cursor is not valid")
        q = q.where(audit_events.c.id < int(cursor))
    rows = conn.execute(q).all()
    page = rows[:limit]
    return {
        "items": [audit_out(r) for r in page],
        "next_cursor": str(page[-1].id) if len(rows) > limit else None,
    }


def verify_audit(conn: Connection, tenant_id: UUID) -> dict[str, Any]:
    intact, checked = audit.verify_chain(conn, tenant_id)
    head = conn.execute(
        select(audit_events.c.hash)
        .where(audit_events.c.tenant_id == tenant_id)
        .order_by(audit_events.c.id.desc())
        .limit(1)
    ).scalar_one_or_none()
    return {"intact": intact, "events_checked": checked, "head": head}


EXPORT_FIELDS = [
    "event_id",
    "at",
    "actor",
    "action",
    "object_type",
    "object_id",
    "reason",
    "prev_hash",
    "hash",
    "escaped_fields",
]
TEXT_FIELDS = ("actor", "action", "object_type", "object_id", "reason")


def _needs_escape(value: str) -> bool:
    return value[:1] in ("=", "+", "-", "@", "\t", "\r")


def export_audit_rows(tenant_id: UUID, batch: int = 1000) -> Iterator[str]:
    """CSV lines, oldest first, read in batches (one short transaction each).

    Values are exactly those the hash covers: `at` is UTC with microseconds, as in the hash. Text cells
    that a spreadsheet would run as a formula get one leading apostrophe, and the column
    `escaped_fields` names those cells, so the original value is recoverable without guessing."""
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(EXPORT_FIELDS)
    yield buf.getvalue()
    after = 0
    while True:
        with tenant_tx(tenant_id) as conn:
            rows = conn.execute(
                select(audit_events)
                .where(audit_events.c.tenant_id == tenant_id, audit_events.c.id > after)
                .order_by(audit_events.c.id)
                .limit(batch)
            ).all()
        if not rows:
            return
        buf.seek(0)
        buf.truncate()
        for r in rows:
            values = {f: getattr(r, f) or "" for f in TEXT_FIELDS}
            escaped = [f for f in TEXT_FIELDS if _needs_escape(values[f])]
            for f in escaped:
                values[f] = "'" + values[f]
            writer.writerow(
                [
                    str(r.event_id),
                    r.at.astimezone(UTC).isoformat(timespec="microseconds"),
                    values["actor"],
                    values["action"],
                    values["object_type"],
                    values["object_id"],
                    values["reason"],
                    r.prev_hash,
                    r.hash,
                    " ".join(escaped),
                ]
            )
        yield buf.getvalue()
        after = rows[-1].id
