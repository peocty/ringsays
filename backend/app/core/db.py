"""Database engines and tenant scoped transactions.

Engines hide bound parameters in error text, so phone numbers never reach logs through SQL errors.

Every API request touching tenant data runs inside `tenant_tx`, which sets `app.tenant_id` for that
transaction only. Row level security policies read that setting, so a query without it sees no rows.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime
from functools import lru_cache
from uuid import UUID

from sqlalchemy import Connection, Engine, create_engine, text

from app.core.config import settings


@lru_cache(maxsize=4)
def get_engine(url: str | None = None) -> Engine:
    return create_engine(
        url or settings.database_url, pool_pre_ping=True, pool_size=10, max_overflow=10, hide_parameters=True
    )


@lru_cache(maxsize=4)
def get_worker_engine(url: str | None = None) -> Engine:
    return create_engine(
        url or settings.worker_database_url, pool_pre_ping=True, pool_size=4, hide_parameters=True
    )


@lru_cache(maxsize=4)
def get_backoffice_engine(url: str | None = None) -> Engine:
    return create_engine(
        url or settings.backoffice_database_url, pool_pre_ping=True, pool_size=4, hide_parameters=True
    )


@contextmanager
def tenant_tx(tenant_id: UUID, engine: Engine | None = None) -> Iterator[Connection]:
    eng = engine or get_engine()
    with eng.begin() as conn:
        conn.execute(text("SELECT set_config('app.tenant_id', :t, true)"), {"t": str(tenant_id)})
        yield conn


@contextmanager
def user_tx(
    user_id: UUID, phone_hash: str, since: datetime | None, engine: Engine | None = None
) -> Iterator[Connection]:
    """Signed in RingSays user: own identity rows, plus read access to intents addressed to their phone
    created on or after `since` (account creation). Earlier intents belong to a previous holder of the
    number and stay invisible. `since=None` gives access to own identity rows only."""
    eng = engine or get_engine()
    with eng.begin() as conn:
        conn.execute(
            text(
                "SELECT set_config('app.user_id', :u, true), set_config('app.phone_hash', :p, true), "
                "set_config('app.user_since', :s, true)"
            ),
            {"u": str(user_id), "p": phone_hash, "s": since.isoformat() if since else ""},
        )
        yield conn


@contextmanager
def anonymous_tx(engine: Engine | None = None) -> Iterator[Connection]:
    """Transaction with no tenant set; row level security returns no tenant rows."""
    eng = engine or get_engine()
    with eng.begin() as conn:
        yield conn


@contextmanager
def worker_tx(engine: Engine | None = None) -> Iterator[Connection]:
    eng = engine or get_worker_engine()
    with eng.begin() as conn:
        yield conn


@contextmanager
def backoffice_tx(engine: Engine | None = None) -> Iterator[Connection]:
    """Back office role: reads organisation, catalogue and verification rows across tenants (never intents
    or identity). Only the internal back office deployment has this credential."""
    if not settings.backoffice_enabled:
        raise RuntimeError("back office is not enabled in this deployment")
    eng = engine or get_backoffice_engine()
    with eng.begin() as conn:
        yield conn
