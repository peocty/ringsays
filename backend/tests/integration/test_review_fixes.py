"""Regression tests for defects found in independent stage 2 review."""

from __future__ import annotations

import threading
from datetime import timedelta
from uuid import uuid4

import jwt
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select, text, update
from sqlalchemy.exc import DBAPIError

from app.core import auth as auth_mod
from app.core.config import settings
from app.core.db import get_engine, get_worker_engine, tenant_tx, worker_tx
from app.core.tables import outbox
from app.modules.audit import service as audit
from app.modules.intent import jobs
from app.platform import outbox as ob
from app.scripts.seed import SeededTenant

from .conftest import Clock, auth, intent_body, token_for

pytestmark = pytest.mark.integration


def _create_many(client: TestClient, t: SeededTenant, n: int) -> None:
    tok = token_for(client, t)
    for _ in range(n):
        r = client.post("/v1/intents", json=intent_body(t), headers=auth(tok, str(uuid4())))
        assert r.status_code == 201


def test_concurrent_expiry_workers_across_tenants_do_not_deadlock(
    client: TestClient, tenant: SeededTenant, other_tenant: SeededTenant, clock: Clock
) -> None:
    _create_many(client, tenant, 6)
    _create_many(client, other_tenant, 6)
    later = clock.now + timedelta(days=1)
    errors: list[BaseException] = []
    counts: list[int] = []

    def run() -> None:
        try:
            counts.append(jobs.expire_due(later))
        except BaseException as exc:
            errors.append(exc)

    threads = [threading.Thread(target=run) for _ in range(4)]
    for th in threads:
        th.start()
    for th in threads:
        th.join()
    assert not errors, errors
    assert jobs.expire_due(later) == 0, "everything due was expired exactly once"
    with tenant_tx(tenant.tenant_id) as conn:
        intact, _ = audit.verify_chain(conn, tenant.tenant_id)
    assert intact


def test_engines_hide_sql_parameters(database: dict[str, str]) -> None:
    assert get_engine().hide_parameters and get_worker_engine().hide_parameters


def test_unknown_client_does_same_hash_work(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []
    real = auth_mod.verify_secret
    monkeypatch.setattr(auth_mod, "verify_secret", lambda s, h: calls.append(h) or real(s, h))
    r = client.post(
        "/oauth/token",
        data={"grant_type": "client_credentials", "client_id": "cli_nobody", "client_secret": "x"},
    )
    assert r.status_code == 401 and calls == [auth_mod._DUMMY_HASH]


def test_revoked_client_loses_access_before_token_expiry(
    client: TestClient,
    tenant: SeededTenant,
    owner_engine,  # type: ignore[no-untyped-def]
) -> None:
    tok = token_for(client, tenant)
    assert client.get(f"/v1/intents/{uuid4()}", headers=auth(tok)).status_code == 404
    with owner_engine.begin() as c:
        c.execute(
            text("UPDATE enterprise.api_clients SET active=false WHERE client_id=:c"), {"c": tenant.client_id}
        )
    auth_mod.clear_client_status_cache()
    r = client.get(f"/v1/intents/{uuid4()}", headers=auth(tok))
    assert r.status_code == 401


def test_token_without_scope_claim_rejected(client: TestClient, tenant: SeededTenant) -> None:
    forged = jwt.encode(
        {
            "iss": settings.jwt_issuer,
            "aud": auth_mod.AUDIENCE,
            "sub": tenant.client_id,
            "tid": str(tenant.tenant_id),
            "iat": 1_900_000_000,
            "exp": 4_000_000_000,
        },
        settings.jwt_secret,
        algorithm=auth_mod.ALGORITHM,
    )
    assert client.get(f"/v1/intents/{uuid4()}", headers=auth(forged)).status_code == 401


def _drain(clock: Clock) -> None:
    with worker_tx() as conn:
        conn.execute(update(outbox).where(outbox.c.published_at.is_(None)).values(published_at=clock.now))


def test_relay_stops_at_first_failure_preserving_order(
    client: TestClient, tenant: SeededTenant, clock: Clock
) -> None:
    _drain(clock)
    _create_many(client, tenant, 2)
    with worker_tx() as conn:
        first_id = conn.execute(
            select(outbox.c.id).where(outbox.c.published_at.is_(None)).order_by(outbox.c.id).limit(1)
        ).scalar_one()

    class FailFirst:
        def __init__(self) -> None:
            self.sent: list[int] = []

        def publish(self, subject: str, payload: bytes, msg_id: str | None = None) -> None:
            raise ConnectionError("broker down")

    with worker_tx() as conn:
        assert ob.relay_once(conn, FailFirst(), clock.now) == 0
        pending = conn.execute(
            select(outbox).where(outbox.c.published_at.is_(None)).order_by(outbox.c.id)
        ).all()
    assert [p.attempts for p in pending] == [1, 0], "second event must not be attempted before first"
    assert pending[0].id == first_id
    ok = ob.MockPublisher()
    with worker_tx() as conn:
        assert ob.relay_once(conn, ok, clock.now) == 2


def test_dead_letter_after_max_attempts(client: TestClient, tenant: SeededTenant, clock: Clock) -> None:
    _drain(clock)
    _create_many(client, tenant, 2)
    subject = f"ringsays.{tenant.tenant_id}.intent.created"
    with worker_tx() as conn:
        first = conn.execute(
            select(outbox.c.id).where(outbox.c.published_at.is_(None)).order_by(outbox.c.id).limit(1)
        ).scalar_one()
        conn.execute(update(outbox).where(outbox.c.id == first).values(attempts=ob.MAX_ATTEMPTS - 1))

    class FailId:
        def __init__(self) -> None:
            self.calls = 0

        def publish(self, s: str, payload: bytes, msg_id: str | None = None) -> None:
            self.calls += 1
            if self.calls == 1:
                raise ConnectionError("poison message")

    with worker_tx() as conn:
        assert ob.relay_once(conn, FailId(), clock.now) == 1
        dead = conn.execute(select(outbox).where(outbox.c.id == first)).one()
    assert dead.dead_at is not None and dead.subject == subject


def test_only_one_relay_runs_at_a_time(database: dict[str, str], clock: Clock) -> None:
    with worker_tx() as holder:
        holder.execute(text("SELECT pg_advisory_xact_lock(hashtext(:k))"), {"k": ob.RELAY_LOCK})
        with worker_tx() as second:
            assert ob.relay_once(second, ob.MockPublisher(), clock.now) == 0


def test_audit_truncate_rejected(owner_engine) -> None:  # type: ignore[no-untyped-def]
    with pytest.raises(DBAPIError, match="append only"):
        with owner_engine.begin() as c:
            c.execute(text("TRUNCATE audit.audit_events"))


def test_anchored_head_detects_removed_tail(client: TestClient, tenant: SeededTenant) -> None:
    _create_many(client, tenant, 1)
    with tenant_tx(tenant.tenant_id) as conn:
        assert audit.verify_chain(conn, tenant.tenant_id, anchored_head=None)[0]
        assert not audit.verify_chain(conn, tenant.tenant_id, anchored_head="e" * 64)[0]
    with worker_tx() as conn:
        heads = audit.chain_heads(conn)
    with tenant_tx(tenant.tenant_id) as conn:
        assert audit.verify_chain(conn, tenant.tenant_id, anchored_head=heads[str(tenant.tenant_id)])[0]
