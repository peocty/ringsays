"""Database engines and tenant scoped transactions.

Engines hide bound parameters in error text, so phone numbers never reach logs through SQL errors.

Every API request touching tenant data runs inside `tenant_tx`, which sets `app.tenant_id` for that
transaction only. Row level security policies read that setting, so a query without it sees no rows.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
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


@contextmanager
def tenant_tx(tenant_id: UUID, engine: Engine | None = None) -> Iterator[Connection]:
    eng = engine or get_engine()
    with eng.begin() as conn:
        conn.execute(text("SELECT set_config('app.tenant_id', :t, true)"), {"t": str(tenant_id)})
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
