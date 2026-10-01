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
        "backoffice": _url_for("ringsays_backoffice", "ringsays_backoffice"),
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
    settings.backoffice_database_url = urls["backoffice"]
    db.get_engine.cache_clear()
    db.get_worker_engine.cache_clear()
    db.get_backoffice_engine.cache_clear()
    yield urls
    db.get_engine().dispose()
    db.get_worker_engine().dispose()
    db.get_backoffice_engine().dispose()
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
    from app.modules.admin import api as admin_api
    from app.modules.client import api as client_api
    from app.modules.intent import api

    app.dependency_overrides[api.clock] = clock
    app.dependency_overrides[client_api.clock] = clock
    app.dependency_overrides[admin_api.clock] = clock
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


@pytest.fixture(autouse=True)
def _isolated_limits_and_adapters() -> Iterator[None]:
    """Fresh Redis test database and MOCK adapters per test; generous limits unless a test lowers them."""
    import redis as redis_lib

    from app.core import auth as auth_mod
    from app.modules.delivery import adapters
    from app.platform import ratelimit

    client = redis_lib.Redis.from_url("redis://127.0.0.1:6379/15", socket_timeout=0.5)
    try:
        client.flushdb()
    except redis_lib.RedisError:
        pass
    ratelimit.set_limiter(ratelimit.Limiter(client))
    saved = (
        settings.recipient_intents_per_tenant_per_day,
        settings.tenant_urgent_per_day,
        settings.tenant_creates_per_minute,
    )
    settings.recipient_intents_per_tenant_per_day = 1000
    from app.modules.identity import service as identity

    adapters.configure(adapters.MockRecipientDirectory(), adapters.MockPushSender())
    import tempfile

    from app.platform import blobs

    blob_dir = tempfile.mkdtemp(prefix="ringsays-blobs-")
    blobs.set_store(blobs.LocalBlobStore(blob_dir))
    identity.configure_sms(identity.MockSmsSender())
    identity.clear_device_cache()
    auth_mod.clear_client_status_cache()
    yield
    (
        settings.recipient_intents_per_tenant_per_day,
        settings.tenant_urgent_per_day,
        settings.tenant_creates_per_minute,
    ) = saved
    ratelimit.set_limiter(None)
    blobs.set_store(None)


class AppUser:
    """A signed in RingSays app user with a real P-256 device key (as the phone would hold)."""

    def __init__(self, client: TestClient, phone: str, apns: str | None = "apns-test-token") -> None:
        import base64

        from cryptography.hazmat.primitives import serialization
        from cryptography.hazmat.primitives.asymmetric import ec

        from app.modules.identity import service as identity

        self.client, self.phone = client, phone
        self.key = ec.generate_private_key(ec.SECP256R1())
        public = self.key.public_key().public_bytes(
            serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo
        )
        r = client.post("/v1/auth/otp", json={"phone": phone, "locale": "ar"})
        assert r.status_code == 202, r.text
        sms = identity.get_sms()
        assert isinstance(sms, identity.MockSmsSender)
        code = sms.last_code_for(phone)
        r = client.post(
            "/v1/auth/verify",
            json={
                "challenge_id": r.json()["challenge_id"],
                "code": code,
                "device": {
                    "platform": "IOS",
                    "public_key": base64.b64encode(public).decode(),
                    "app_version": "0.1.0",
                    "push": {"apns": apns},
                },
            },
        )
        assert r.status_code == 200, r.text
        self.tokens = r.json()

    @property
    def headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.tokens['access_token']}", "Accept-Language": "ar"}

    def sign(self, message: str) -> str:
        import base64

        from cryptography.hazmat.primitives import hashes
        from cryptography.hazmat.primitives.asymmetric import ec

        return base64.b64encode(self.key.sign(message.encode(), ec.ECDSA(hashes.SHA256()))).decode()


class PortalPerson:
    """A person signed in to the portal through the MOCK OpenID Connect issuer."""

    def __init__(self, client: TestClient, email: str) -> None:
        from app.modules.devoidc import issuer

        self.client, self.email = client, email
        self.token = issuer.tokens_for(email)["access_token"]
        r = client.get("/admin/v1/me", headers=self.headers)
        assert r.status_code == 200, r.text
        self.me = r.json()

    @property
    def headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.token}"}


def unique_email(prefix: str = "person") -> str:
    import uuid as _uuid

    return f"{prefix}.{_uuid.uuid4().hex[:10]}@mockbank.example"


def portal_member(
    client: TestClient,
    owner_engine: Engine,
    t: SeededTenant,
    roles: list[str],
    *,
    agent_id: str | None = None,
    email: str | None = None,
) -> PortalPerson:
    from app.scripts.seed import invite_portal_user

    email = email or unique_email(roles[0].lower())
    invite_portal_user(owner_engine, t.tenant_id, email, roles, agent_id=agent_id)
    return PortalPerson(client, email)


def staff_member(client: TestClient, owner_engine: Engine, roles: list[str]) -> PortalPerson:
    import uuid as _uuid

    from app.scripts.seed import seed_staff

    email = f"staff.{_uuid.uuid4().hex[:10]}@ringsays.example"
    seed_staff(owner_engine, email, roles)
    return PortalPerson(client, email)
