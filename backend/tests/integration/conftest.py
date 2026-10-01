"""Integration fixtures: a fresh PostgreSQL database per test session, migrated, with real roles.

Requires a reachable PostgreSQL superuser URL in RINGSAYS_TEST_PG_ADMIN_URL
(default postgresql+psycopg://postgres@127.0.0.1:5432/postgres). Tests skip if unreachable.
"""

from __future__ import annotations

import os
import subprocess
import sys
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine, make_url

from app.core import db
from app.core.config import settings
from app.scripts.seed import SeededTenant, seed_tenant

BACKEND = Path(__file__).resolve().parents[2]
ROLES_SQL = BACKEND.parent / "infra/sql/00-roles.sql"
ADMIN_URL = os.environ.get(
    "RINGSAYS_TEST_PG_ADMIN_URL", "postgresql+psycopg://postgres@127.0.0.1:5432/postgres"
)
TEST_DB = "ringsays_test"


def _url_for(user: str, password: str) -> str:
    u = make_url(ADMIN_URL)
    return str(
        u.set(username=user, password=password, database=TEST_DB).render_as_string(hide_password=False)
    )


@pytest.fixture(scope="session")
def database() -> Iterator[dict[str, str]]:
    try:
        admin = create_engine(ADMIN_URL, isolation_level="AUTOCOMMIT")
        with admin.connect() as c:
            c.execute(text("SELECT 1"))
    except Exception as exc:
        pytest.skip(f"PostgreSQL not reachable for integration tests: {exc}")
    with admin.connect() as c:
        c.execute(text(ROLES_SQL.read_text()))
        c.execute(text(f"DROP DATABASE IF EXISTS {TEST_DB} WITH (FORCE)"))
        c.execute(text(f"CREATE DATABASE {TEST_DB} OWNER ringsays_owner"))
    urls = {
        "owner": _url_for("ringsays_owner", "ringsays_owner"),
        "app": _url_for("ringsays_app", "ringsays_app"),
        "worker": _url_for("ringsays_worker", "ringsays_worker"),
    }
    env = {**os.environ, "RINGSAYS_MIGRATION_DATABASE_URL": urls["owner"]}
    subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        cwd=BACKEND,
        env=env,
        check=True,
        capture_output=True,
    )
    settings.database_url = urls["app"]
    settings.worker_database_url = urls["worker"]
    settings.migration_database_url = urls["owner"]
    db.get_engine.cache_clear()
    db.get_worker_engine.cache_clear()
    yield urls
    db.get_engine().dispose()
    db.get_worker_engine().dispose()
    with admin.connect() as c:
        c.execute(text(f"DROP DATABASE IF EXISTS {TEST_DB} WITH (FORCE)"))
    admin.dispose()


@pytest.fixture(scope="session")
def owner_engine(database: dict[str, str]) -> Iterator[Engine]:
    eng = create_engine(database["owner"])
    yield eng
    eng.dispose()


@pytest.fixture
def tenant(owner_engine: Engine) -> SeededTenant:
    return seed_tenant(owner_engine)


@pytest.fixture
def other_tenant(owner_engine: Engine) -> SeededTenant:
    return seed_tenant(owner_engine, name_en="Other Mock Bank")


FIXED_NOW = datetime(2026, 10, 4, 7, 0, tzinfo=UTC)


class Clock:
    def __init__(self) -> None:
        self.now = FIXED_NOW

    def __call__(self) -> datetime:
        return self.now


@pytest.fixture
def clock() -> Clock:
    return Clock()


@pytest.fixture
def client(database: dict[str, str], clock: Clock) -> Iterator[TestClient]:
    from app.main import app
    from app.modules.intent import api

    app.dependency_overrides[api.clock] = clock
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


def token_for(client: TestClient, t: SeededTenant, scope: str | None = None) -> str:
    data = {"grant_type": "client_credentials", "client_id": t.client_id, "client_secret": t.client_secret}
    if scope:
        data["scope"] = scope
    r = client.post("/oauth/token", data=data)
    assert r.status_code == 200, r.text
    return str(r.json()["access_token"])


def auth(token: str, idem: str | None = None) -> dict[str, str]:
    h = {"Authorization": f"Bearer {token}"}
    if idem:
        h["Idempotency-Key"] = idem
    return h


def intent_body(t: SeededTenant, **overrides: Any) -> dict[str, Any]:
    body: dict[str, Any] = {
        "to": {"phone": "+966500000001"},
        "agent_id": t.agent_id,
        "purpose_code": "MORTGAGE.DOC.CLARIFY",
        "masked_reference": "8291",
        "priority": "NORMAL",
        "expected_duration_min": 5,
        "valid_from": "2026-10-04T07:30:00Z",
        "valid_until": "2026-10-04T08:00:00Z",
        "channel_preference": ["SDK", "PRECALL_PUSH", "PSTN"],
        "language": "ar",
    }
    body.update(overrides)
    return body
