"""Background jobs for intents. Run under worker role (row level security bypassed; never from API)."""

from __future__ import annotations

import logging
from datetime import datetime
from uuid import UUID

from sqlalchemy import Engine, select

from app.core.db import worker_tx
from app.core.tables import intents

from . import repo, service
from . import state_machine as sm

log = logging.getLogger(__name__)


def expire_due(now: datetime, batch: int = 500, engine: Engine | None = None) -> int:
    """Expire intents whose validity window has passed. Returns number expired.

    Candidates are read without locks; each intent is then expired in its own short transaction with a
    row lock taken by `service.apply`. One transaction never spans two tenants, so concurrent workers
    cannot deadlock on per tenant audit locks, and API writes are blocked for one intent at most.
    An intent changed by an API call in between is simply skipped by state machine (no longer due).
    """
    with worker_tx(engine) as conn:
        ids = (
            conn.execute(
                select(intents.c.id)
                .where(intents.c.status.in_([s.value for s in sm.EXPIRABLE]), intents.c.valid_until < now)
                .order_by(intents.c.valid_until)
                .limit(batch)
            )
            .scalars()
            .all()
        )
    expired = 0
    for intent_id in ids:
        try:
            expired += _expire_one(intent_id, now, engine)
        except Exception as exc:
            log.error("expiry failed for intent %s: %s", intent_id, type(exc).__name__)
    return expired


def _expire_one(intent_id: UUID, now: datetime, engine: Engine | None) -> int:
    with worker_tx(engine) as conn:
        current, _ = repo.load(conn, intent_id, for_update=True)
        if not sm.is_expired(current, now):
            return 0  # changed by an API call since candidates were read
        service.apply(
            conn, intent_id, lambda i: sm.expire_if_due(i, now), "system:expiry", "intent.expire", now
        )
        return 1
