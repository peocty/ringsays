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
    def publish(self, subject: str, payload: bytes, msg_id: str | None = None) -> None: ...


@dataclass
class MockPublisher:
    """In memory publisher for local runs and tests. Never reports delivery to any real broker."""

    published: list[tuple[str, dict[str, Any]]] = field(default_factory=list)
    fail_subjects: set[str] = field(default_factory=set)

    def publish(self, subject: str, payload: bytes, msg_id: str | None = None) -> None:
        if subject in self.fail_subjects:
            raise ConnectionError(f"MockPublisher configured to fail for {subject}")
        self.published.append((subject, json.loads(payload)))


STREAM = "RINGSAYS_EVENTS"
STREAM_SUBJECTS = ["ringsays.>"]


class NatsPublisher:
    """NATS JetStream publisher holding one connection for its lifetime.

    On first connect it makes sure the events stream exists (file storage, bounded age, deduplication
    window), creating or updating it, so a fresh server works without manual setup. Each event carries
    its outbox id as `Nats-Msg-Id`: a relay retry after a lost acknowledgement is dropped by the server
    instead of published twice. Tested against a real nats-server (tests/test_nats_publisher.py).
    """

    def __init__(
        self, url: str, timeout_s: float = 5.0, replicas: int = 1, max_age_s: int = 7 * 86400
    ) -> None:
        self._url = url
        self._timeout = timeout_s
        self._replicas = replicas
        self._max_age_s = max_age_s
        self._loop = asyncio.new_event_loop()
        self._nc: Any = None
        self._js: Any = None

    async def _connect(self) -> None:
        import nats
        from nats.js.api import DiscardPolicy, RetentionPolicy, StorageType, StreamConfig
        from nats.js.errors import NotFoundError

        async def on_error(exc: Exception) -> None:
            log.warning("nats: %s", type(exc).__name__)

        # Fail fast: a wrong password or unreachable server must not stall the worker's pass; the next
        # publish reconnects (relay keeps the event pending and retries in order).
        self._nc = await nats.connect(
            self._url,
            connect_timeout=self._timeout,
            allow_reconnect=False,
            max_reconnect_attempts=1,  # 0 would mean: retry a refused server forever
            reconnect_time_wait=0.5,
            error_cb=on_error,
        )
        self._js = self._nc.jetstream(timeout=self._timeout)
        config = StreamConfig(
            name=STREAM,
            subjects=STREAM_SUBJECTS,
            retention=RetentionPolicy.LIMITS,
            storage=StorageType.FILE,
            discard=DiscardPolicy.OLD,
            max_age=float(self._max_age_s),
            num_replicas=self._replicas,
            duplicate_window=float(15 * 60),
        )
        try:
            await self._js.stream_info(STREAM)
            await self._js.update_stream(config)
        except NotFoundError:
            await self._js.add_stream(config)

    def _ensure(self) -> None:
        if self._nc is None or self._nc.is_closed:
            self._loop.run_until_complete(self._connect())

    def publish(self, subject: str, payload: bytes, msg_id: str | None = None) -> None:
        self._ensure()
        headers = {"Nats-Msg-Id": msg_id} if msg_id else None
        self._loop.run_until_complete(self._js.publish(subject, payload, headers=headers))

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
            publisher.publish(row.subject, json.dumps(row.payload).encode(), msg_id=str(row.id))
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
