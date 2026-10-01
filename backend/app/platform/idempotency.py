"""Idempotency keys for every enterprise write.

Same key and same request body: original response is replayed (HTTP 200 for creates).
Same key with a different body or route: rejected, so a client bug cannot silently overwrite.
Keys are scoped per tenant and checked inside tenant transaction under an advisory lock.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any
from uuid import UUID

from sqlalchemy import Connection, insert, select, text

from app.core.tables import idempotency_keys
from app.modules.intent.errors import IntentError


class IdempotencyMismatch(IntentError):
    code = "idempotency_key_reused"
    http_status = 422


@dataclass(frozen=True, slots=True)
class StoredResponse:
    status: int
    body: dict[str, Any]


def request_hash(route: str, body: Any) -> str:
    raw = json.dumps({"route": route, "body": body}, sort_keys=True, default=str, separators=(",", ":"))
    return hashlib.sha256(raw.encode()).hexdigest()


def lookup(conn: Connection, tenant_id: UUID, key: UUID, route: str, req_hash: str) -> StoredResponse | None:
    conn.execute(text("SELECT pg_advisory_xact_lock(hashtext(:k))"), {"k": f"idem:{tenant_id}:{key}"})
    row = conn.execute(
        select(idempotency_keys).where(
            idempotency_keys.c.tenant_id == tenant_id, idempotency_keys.c.key == key
        )
    ).one_or_none()
    if row is None:
        return None
    if row.route != route or row.request_hash != req_hash:
        raise IdempotencyMismatch("Idempotency-Key was already used with a different request")
    return StoredResponse(status=row.response_status, body=row.response_body)


def store(
    conn: Connection, tenant_id: UUID, key: UUID, route: str, req_hash: str, status: int, body: dict[str, Any]
) -> None:
    conn.execute(
        insert(idempotency_keys).values(
            tenant_id=tenant_id,
            key=key,
            route=route,
            request_hash=req_hash,
            response_status=status,
            response_body=json.loads(json.dumps(body, default=str)),
        )
    )
