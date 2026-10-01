"""Transactional outbox (ADR 0005).

`enqueue` writes an event row in caller's transaction, so event and state change commit together.
`relay_once` runs in a single active worker and publishes pending rows in order, then marks them published.
Payloads must contain identifiers and status only; `enqueue` rejects keys that look like personal data.
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Protocol
from uuid import UUID

from sqlalchemy import Connection, insert, select, text, update

from app.core.tables import outbox

FORBIDDEN_PAYLOAD_KEYS = frozenset(
    {"phone", "to_phone", "name", "display_name", "subject", "message", "email", "address"}
)
MAX_ATTEMPTS = 20
log = logging.getLogger(__name__)


class Publisher(Protocol):
    def publish(self, subject: str, payload: bytes) -> None: ...


@dataclass
class MockPublisher:
    """In memory publisher for local runs and tests. Never reports delivery to any real broker."""

    published: list[tuple[str, dict[str, Any]]] = field(default_factory=list)
    fail_subjects: set[str] = field(default_factory=set)

    def publish(self, subject: str, payload: bytes) -> None:
        if subject in self.fail_subjects:
            raise ConnectionError(f"MockPublisher configured to fail for {subject}")
        self.published.append((subject, json.loads(payload)))


class NatsPublisher:
    """NATS JetStream publisher holding one connection for its lifetime.

    Written against nats-py; not exercised in Claude's build workspace (no NATS server available).
    """

    def __init__(self, url: str, timeout_s: float = 5.0) -> None:
        self._url = url
        self._timeout = timeout_s
        self._loop = asyncio.new_event_loop()
        self._nc: Any = None
        self._js: Any = None

    def _ensure(self) -> None:
        if self._nc is None or self._nc.is_closed:
            import nats

            self._nc = self._loop.run_until_complete(nats.connect(self._url, connect_timeout=self._timeout))
            self._js = self._nc.jetstream(timeout=self._timeout)

    def publish(self, subject: str, payload: bytes) -> None:
        self._ensure()
        self._loop.run_until_complete(self._js.publish(subject, payload))

    def close(self) -> None:
        if self._nc is not None and not self._nc.is_closed:
            self._loop.run_until_complete(self._nc.drain())
        self._loop.close()


def subject_for(tenant_id: UUID | None, aggregate: str, event: str) -> str:
    scope = str(tenant_id) if tenant_id else "consumer"
    return f"ringsays.{scope}.{aggregate}.{event}"


def enqueue(conn: Connection, tenant_id: UUID | None, subject: str, payload: Mapping[str, Any]) -> None:
    bad = FORBIDDEN_PAYLOAD_KEYS & set(payload)
    if bad:
        raise ValueError(f"outbox payload must not carry personal data keys: {sorted(bad)}")
    conn.execute(
        insert(outbox).values(
            tenant_id=tenant_id, subject=subject, payload=json.loads(json.dumps(payload, default=str))
        )
    )


RELAY_LOCK = "outbox-relay"


def relay_once(conn: Connection, publisher: Publisher, now: datetime, batch: int = 100) -> int:
    """Publish pending events strictly in commit order. Returns number published.

    Only one relay runs at a time (transaction advisory lock), so events are never reordered between
    workers. Batch stops at first failure, so a later event never overtakes an earlier one. An event that
    fails MAX_ATTEMPTS times is moved to dead letter (`dead_at` set, error logged) and relay continues.
    """
    got_lock = conn.execute(
        text("SELECT pg_try_advisory_xact_lock(hashtext(:k))"), {"k": RELAY_LOCK}
    ).scalar_one()
    if not got_lock:
        return 0
    rows = conn.execute(
        select(outbox.c.id, outbox.c.subject, outbox.c.payload, outbox.c.attempts)
        .where(outbox.c.published_at.is_(None), outbox.c.dead_at.is_(None))
        .order_by(outbox.c.id)
        .limit(batch)
    ).all()
    sent = 0
    for row in rows:
        try:
            publisher.publish(row.subject, json.dumps(row.payload).encode())
        except Exception as exc:
            attempts = row.attempts + 1
            dead = attempts >= MAX_ATTEMPTS
            conn.execute(
                update(outbox)
                .where(outbox.c.id == row.id)
                .values(attempts=attempts, last_error=str(exc)[:500], dead_at=now if dead else None)
            )
            if dead:
                log.error("outbox event dead lettered id=%s subject=%s", row.id, row.subject)
                continue
            break
        conn.execute(
            update(outbox)
            .where(outbox.c.id == row.id)
            .values(published_at=now, attempts=row.attempts + 1, last_error=None)
        )
        sent += 1
    return sent
