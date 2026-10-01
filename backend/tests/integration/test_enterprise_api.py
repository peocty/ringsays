from __future__ import annotations

from datetime import timedelta
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select, text, update
from sqlalchemy.exc import DBAPIError

from app.core.db import tenant_tx, worker_tx
from app.core.tables import audit_events, outbox
from app.modules.audit import service as audit
from app.modules.intent import jobs, service
from app.modules.intent import state_machine as sm
from app.modules.intent.domain import Channel, ResponseAction
from app.platform.outbox import MockPublisher, relay_once
from app.scripts.seed import SeededTenant, seed_tenant

from .conftest import Clock, auth, intent_body, token_for

pytestmark = pytest.mark.integration


def _create(client: TestClient, t: SeededTenant, **overrides: object) -> UUID:
    tok = token_for(client, t)
    r = client.post("/v1/intents", json=intent_body(t, **overrides), headers=auth(tok, str(uuid4())))
    assert r.status_code == 201, r.text
    return UUID(r.json()["intent_id"])


# Auth


def test_token_rejects_wrong_secret_and_unknown_client(client: TestClient, tenant: SeededTenant) -> None:
    for cid, secret in [(tenant.client_id, "wrong"), ("cli_unknown", tenant.client_secret)]:
        r = client.post(
            "/oauth/token",
            data={"grant_type": "client_credentials", "client_id": cid, "client_secret": secret},
        )
        assert r.status_code == 401
        assert r.json() == {"error": "invalid_client"}


def test_token_scope_narrowing_and_enforcement(client: TestClient, tenant: SeededTenant) -> None:
    read_only = token_for(client, tenant, scope="intents:read")
    r = client.post("/v1/intents", json=intent_body(tenant), headers=auth(read_only, str(uuid4())))
    assert r.status_code == 403
    r = client.post(
        "/oauth/token",
        data={
            "grant_type": "client_credentials",
            "client_id": tenant.client_id,
            "client_secret": tenant.client_secret,
            "scope": "admin:everything",
        },
    )
    assert r.json() == {"error": "invalid_scope"}


def test_missing_and_garbage_token(client: TestClient) -> None:
    assert client.get(f"/v1/intents/{uuid4()}").status_code == 401
    assert client.get(f"/v1/intents/{uuid4()}", headers=auth("not.a.jwt")).status_code == 401


def test_suspended_tenant_cannot_get_token(client: TestClient, owner_engine, tenant: SeededTenant) -> None:  # type: ignore[no-untyped-def]
    with owner_engine.begin() as c:
        c.execute(text("SELECT set_config('app.tenant_id', :t, true)"), {"t": str(tenant.tenant_id)})
        c.execute(
            text("UPDATE enterprise.tenants SET verification_status='SUSPENDED' WHERE id=:t"),
            {"t": tenant.tenant_id},
        )
    r = client.post(
        "/oauth/token",
        data={
            "grant_type": "client_credentials",
            "client_id": tenant.client_id,
            "client_secret": tenant.client_secret,
        },
    )
    assert r.status_code == 401


# Create and read


def test_create_and_get(client: TestClient, tenant: SeededTenant) -> None:
    tok = token_for(client, tenant)
    r = client.post("/v1/intents", json=intent_body(tenant), headers=auth(tok, str(uuid4())))
    assert r.status_code == 201
    created = r.json()
    assert created["status"] == "REQUESTED"
    assert created["verification_level"] == "ORG_AGENT_NUMBER"
    got = client.get(f"/v1/intents/{created['intent_id']}", headers=auth(tok)).json()
    assert got["purpose_code"] == "MORTGAGE.DOC.CLARIFY"
    assert got["intent_source"] == "DECLARED"
    assert got["timeline"][0]["to_status"] == "REQUESTED"
    assert "+966500000001" not in r.text + str(got), "recipient phone must not be echoed"


def test_verification_level_without_verified_number(client: TestClient, owner_engine) -> None:  # type: ignore[no-untyped-def]
    t = seed_tenant(owner_engine, verified_number=False)
    tok = token_for(client, t)
    r = client.post("/v1/intents", json=intent_body(t), headers=auth(tok, str(uuid4())))
    assert r.json()["verification_level"] == "ORG"


def test_pending_tenant_cannot_send(client: TestClient, owner_engine) -> None:  # type: ignore[no-untyped-def]
    t = seed_tenant(owner_engine, verification_status="PENDING")
    tok = token_for(client, t)
    r = client.post("/v1/intents", json=intent_body(t), headers=auth(tok, str(uuid4())))
    assert r.status_code == 403
    assert r.json()["code"] == "tenant_not_active"


@pytest.mark.parametrize(
    "overrides,status,code",
    [
        ({"priority": "URGENT"}, 422, "rule_violation"),
        ({"expected_duration_min": 30}, 422, "rule_violation"),
        ({"channel_preference": ["VOIP"]}, 422, "rule_violation"),
        ({"purpose_code": "NOT.IN.CATALOGUE"}, 422, "unknown_purpose_code"),
        ({"agent_id": "agt_nobody"}, 422, "unknown_agent"),
        ({"masked_reference": "SA0380000000608010167519"}, 400, "validation_failed"),
        ({"to": {"phone": "0500000000"}}, 400, "validation_failed"),
        ({"unexpected_field": 1}, 400, "validation_failed"),
    ],
)
def test_create_rejections(
    client: TestClient, tenant: SeededTenant, overrides: dict[str, object], status: int, code: str
) -> None:
    tok = token_for(client, tenant)
    r = client.post("/v1/intents", json=intent_body(tenant, **overrides), headers=auth(tok, str(uuid4())))
    assert r.status_code == status, r.text
    assert r.headers["content-type"].startswith("application/problem+json")
    assert r.json()["code"] == code


def test_idempotency_replay_and_mismatch(client: TestClient, tenant: SeededTenant) -> None:
    tok = token_for(client, tenant)
    key = str(uuid4())
    first = client.post("/v1/intents", json=intent_body(tenant), headers=auth(tok, key))
    again = client.post("/v1/intents", json=intent_body(tenant), headers=auth(tok, key))
    assert first.status_code == 201 and again.status_code == 200
    assert first.json() == again.json()
    changed = client.post("/v1/intents", json=intent_body(tenant, priority="LOW"), headers=auth(tok, key))
    assert changed.status_code == 422
    assert changed.json()["code"] == "idempotency_key_reused"


def test_missing_idempotency_key(client: TestClient, tenant: SeededTenant) -> None:
    tok = token_for(client, tenant)
    r = client.post("/v1/intents", json=intent_body(tenant), headers=auth(tok))
    assert r.status_code == 400


# Lifecycle


def test_full_lifecycle_with_outbox_and_audit(client: TestClient, tenant: SeededTenant, clock: Clock) -> None:
    tok = token_for(client, tenant)
    intent_id = _create(client, tenant)
    with tenant_tx(tenant.tenant_id) as conn:
        service.mark_delivered(conn, intent_id, Channel.SDK, clock.now)
        service.apply_receiver(
            conn, intent_id, lambda i: sm.respond(i, ResponseAction.ACCEPT, clock.now), "user:test", clock.now
        )
    r = client.post(f"/v1/intents/{intent_id}/calling", headers=auth(tok, str(uuid4())))
    assert r.status_code == 202 and r.json()["status"] == "IN_PROGRESS"
    r = client.post(
        f"/v1/intents/{intent_id}/outcome", json={"code": "RESOLVED"}, headers=auth(tok, str(uuid4()))
    )
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "COMPLETED"
    assert [e["to_status"] for e in body["timeline"]] == [
        "REQUESTED",
        "DELIVERED",
        "ACCEPTED",
        "IN_PROGRESS",
        "COMPLETED",
    ]
    with worker_tx() as conn:
        subjects = (
            conn.execute(select(outbox.c.subject).where(outbox.c.tenant_id == tenant.tenant_id))
            .scalars()
            .all()
        )
    assert subjects.count(f"ringsays.{tenant.tenant_id}.intent.created") == 1
    assert subjects.count(f"ringsays.{tenant.tenant_id}.intent.status_changed") == 4
    with tenant_tx(tenant.tenant_id) as conn:
        intact, n = audit.verify_chain(conn, tenant.tenant_id)
        assert intact and n == 5


def test_invalid_transitions_return_409(client: TestClient, tenant: SeededTenant) -> None:
    tok = token_for(client, tenant)
    intent_id = _create(client, tenant)
    r = client.post(f"/v1/intents/{intent_id}/calling", headers=auth(tok, str(uuid4())))
    assert r.status_code == 409 and r.json()["code"] == "invalid_transition"
    assert client.post(f"/v1/intents/{intent_id}/cancel", headers=auth(tok, str(uuid4()))).status_code == 200
    r = client.post(f"/v1/intents/{intent_id}/cancel", headers=auth(tok, str(uuid4())))
    assert r.status_code == 409


def test_cancel_replay_with_same_key(client: TestClient, tenant: SeededTenant) -> None:
    tok = token_for(client, tenant)
    intent_id = _create(client, tenant)
    key = str(uuid4())
    a = client.post(f"/v1/intents/{intent_id}/cancel", headers=auth(tok, key))
    b = client.post(f"/v1/intents/{intent_id}/cancel", headers=auth(tok, key))
    assert a.status_code == b.status_code == 200
    assert a.json() == b.json()


def test_unknown_intent_404(client: TestClient, tenant: SeededTenant) -> None:
    tok = token_for(client, tenant)
    r = client.get(f"/v1/intents/{uuid4()}", headers=auth(tok))
    assert r.status_code == 404 and r.json()["code"] == "not_found"


# Expiry, outbox relay, audit protection


def test_expiry_job(client: TestClient, tenant: SeededTenant, clock: Clock) -> None:
    tok = token_for(client, tenant)
    intent_id = _create(client, tenant)
    assert jobs.expire_due(clock.now + timedelta(minutes=30)) == 0
    assert jobs.expire_due(clock.now + timedelta(hours=2)) >= 1
    got = client.get(f"/v1/intents/{intent_id}", headers=auth(tok)).json()
    assert got["status"] == "EXPIRED"
    assert got["timeline"][-1]["actor"] == "SYSTEM"


def test_outbox_relay_publishes_and_retries(client: TestClient, tenant: SeededTenant, clock: Clock) -> None:
    _create(client, tenant)
    with worker_tx() as conn:
        conn.execute(update(outbox).where(outbox.c.published_at.is_(None)).values(published_at=clock.now))
    intent_id = _create(client, tenant)
    created_subject = f"ringsays.{tenant.tenant_id}.intent.created"
    failing = MockPublisher(fail_subjects={created_subject})
    with worker_tx() as conn:
        assert relay_once(conn, failing, clock.now) == 0
        row = conn.execute(select(outbox).where(outbox.c.published_at.is_(None))).one()
        assert row.attempts == 1 and "MockPublisher" in row.last_error
    ok = MockPublisher()
    with worker_tx() as conn:
        assert relay_once(conn, ok, clock.now) == 1
    subject, payload = ok.published[0]
    assert subject == created_subject
    assert payload["intent_id"] == str(intent_id)
    assert not {"phone", "to_phone", "subject"} & set(payload)


def test_audit_is_append_only(client: TestClient, tenant: SeededTenant) -> None:
    _create(client, tenant)
    with pytest.raises(DBAPIError, match=r"append only|permission denied"):
        with tenant_tx(tenant.tenant_id) as conn:
            conn.execute(update(audit_events).values(actor="tampered"))
    with pytest.raises(DBAPIError):
        with worker_tx() as conn:
            conn.execute(text("DELETE FROM audit.audit_events"))
