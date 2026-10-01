"""Regression tests for defects found in independent stage 3 review."""

from __future__ import annotations

import time
from datetime import timedelta
from typing import Any
from uuid import UUID, uuid4

import pytest
import redis
from fastapi.testclient import TestClient
from sqlalchemy import select, text

from app.core.db import anonymous_tx, tenant_tx, worker_tx
from app.core.tables import delivery_attempts, webhook_deliveries
from app.modules.context.service import TokenRefused
from app.modules.delivery import adapters
from app.modules.delivery import service as delivery
from app.modules.delivery.adapters import MockPushSender, MockRecipientDirectory, PushTarget, Recipient
from app.modules.intent import service as intent_service
from app.modules.intent import state_machine as sm
from app.modules.intent.domain import ResponseAction
from app.modules.preference.engine import Decision, Preferences, Rule
from app.modules.webhooks import service as webhooks
from app.modules.webhooks.service import MockHttpSender
from app.platform import ratelimit
from app.scripts.seed import SeededTenant

from .conftest import Clock, auth, intent_body, token_for

pytestmark = pytest.mark.integration
PHONE = "+966500000001"
OPENS = timedelta(minutes=30)


def _create(client: TestClient, t: SeededTenant, **overrides: Any) -> dict[str, Any]:
    r = client.post(
        "/v1/intents", json=intent_body(t, **overrides), headers=auth(token_for(client, t), str(uuid4()))
    )
    assert r.status_code == 201, r.text
    return dict(r.json())


def _trail(t: SeededTenant, intent_id: str) -> list[tuple[str, str]]:
    with tenant_tx(t.tenant_id) as conn:
        rows = conn.execute(
            select(delivery_attempts.c.channel, delivery_attempts.c.outcome)
            .where(delivery_attempts.c.intent_id == UUID(intent_id))
            .order_by(delivery_attempts.c.id)
        ).all()
    return [(r.channel, r.outcome) for r in rows]


# 1. Token resolve hardening


def test_null_device_cannot_bypass_binding(client: TestClient, tenant: SeededTenant, clock: Clock) -> None:
    token = _create(client, tenant)["context_token"]
    with anonymous_tx() as conn:
        delivery.deliver_via_sdk(conn, token, uuid4(), clock.now + OPENS)
    with anonymous_tx() as conn:
        import hashlib

        h = hashlib.sha256(token.encode()).hexdigest()
        outcome = conn.execute(
            text("SELECT outcome FROM context.resolve_token(:h, NULL, now())"), {"h": h}
        ).scalar_one()
    assert outcome == "unknown"
    with pytest.raises(TokenRefused), anonymous_tx() as conn:
        delivery.deliver_via_sdk(conn, token, uuid4(), clock.now + OPENS)


def test_resolve_function_not_executable_by_worker_role(database: dict[str, str]) -> None:
    with pytest.raises(Exception, match="permission denied"), worker_tx() as conn:
        conn.execute(text("SELECT * FROM context.resolve_token('x', gen_random_uuid(), now())"))


def test_caller_clock_cannot_revive_expired_token(
    client: TestClient, tenant: SeededTenant, owner_engine: Any
) -> None:
    token = _create(client, tenant)["context_token"]
    with owner_engine.begin() as c:
        c.execute(text("UPDATE context.context_tokens SET expires_at = now() - interval '1 hour'"))
    import hashlib

    with anonymous_tx() as conn:
        outcome = conn.execute(
            text(
                "SELECT outcome FROM context.resolve_token(:h, gen_random_uuid(), now() - interval '1 day')"
            ),
            {"h": hashlib.sha256(token.encode()).hexdigest()},
        ).scalar_one()
    assert outcome == "expired"


# 2. Pre call push only after successful transition and within receiver rules


def _delivered_by_push(
    client: TestClient, t: SeededTenant, clock: Clock, prefs: Preferences
) -> tuple[str, Any]:
    created = _create(client, t, channel_preference=["PRECALL_PUSH", "PSTN"])
    recipient = Recipient(uuid4(), (PushTarget(uuid4(), "IOS", "tok"),), prefs)
    push = MockPushSender()
    adapters.configure(MockRecipientDirectory({PHONE: recipient}), push)
    delivery.deliver_due(clock.now + OPENS, adapters.get_directory(), push)
    return created["intent_id"], push


def test_refused_calling_sends_no_push(client: TestClient, tenant: SeededTenant, clock: Clock) -> None:
    intent_id, push = _delivered_by_push(client, tenant, clock, Preferences())
    clock.now += OPENS
    tok = token_for(client, tenant)
    for _ in range(3):
        r = client.post(f"/v1/intents/{intent_id}/calling", headers=auth(tok, str(uuid4())))
        assert r.status_code == 422  # receiver has not agreed
    assert [k for _, k, _ in push.sent] == ["INTENT"], "no PRECALL pushes for refused calls"


def test_calling_push_respects_receiver_block(client: TestClient, tenant: SeededTenant, clock: Clock) -> None:
    intent_id, push = _delivered_by_push(client, tenant, clock, Preferences())
    with tenant_tx(tenant.tenant_id) as conn:
        intent_service.apply_receiver(
            conn,
            UUID(intent_id),
            lambda i: sm.respond(i, ResponseAction.ACCEPT, clock.now + OPENS),
            "user:test",
            clock.now + OPENS,
        )
    blocked = Recipient(
        uuid4(),
        (PushTarget(uuid4(), "IOS", "tok"),),
        Preferences(rules=(Rule(action=Decision.BLOCK, category="BANK"),)),
    )
    adapters.configure(MockRecipientDirectory({PHONE: blocked}), push)
    clock.now += OPENS
    r = client.post(f"/v1/intents/{intent_id}/calling", headers=auth(token_for(client, tenant), str(uuid4())))
    assert r.status_code == 202
    assert [k for _, k, _ in push.sent] == ["INTENT"]
    assert _trail(tenant, intent_id)[-1] == ("PRECALL_PUSH", "SKIPPED")


def test_calling_replay_sends_no_second_push(client: TestClient, tenant: SeededTenant, clock: Clock) -> None:
    intent_id, push = _delivered_by_push(client, tenant, clock, Preferences())
    with tenant_tx(tenant.tenant_id) as conn:
        intent_service.apply_receiver(
            conn,
            UUID(intent_id),
            lambda i: sm.respond(i, ResponseAction.ACCEPT, clock.now + OPENS),
            "user:test",
            clock.now + OPENS,
        )
    clock.now += OPENS
    tok, key = token_for(client, tenant), str(uuid4())
    client.post(f"/v1/intents/{intent_id}/calling", headers=auth(tok, key))
    client.post(f"/v1/intents/{intent_id}/calling", headers=auth(tok, key))
    assert [k for _, k, _ in push.sent] == ["INTENT", "PRECALL"]


# 3. One bad row never stops other work


def test_bad_recipient_data_isolated(
    client: TestClient, tenant: SeededTenant, other_tenant: SeededTenant, clock: Clock
) -> None:
    bad = _create(client, tenant, channel_preference=["PRECALL_PUSH", "PSTN"])
    good = _create(
        client, other_tenant, to={"phone": "+966500000222"}, channel_preference=["PRECALL_PUSH", "PSTN"]
    )
    broken = Recipient(uuid4(), (PushTarget(uuid4(), "IOS", "t"),), Preferences(timezone="Asia/Riyad"))
    from app import worker

    adapters.configure(MockRecipientDirectory({PHONE: broken}), MockPushSender())
    result = worker.tick(
        clock.now + OPENS, __import__("app.platform.outbox", fromlist=["x"]).MockPublisher(), MockHttpSender()
    )
    assert result["errors"] >= 1
    assert _trail(tenant, bad["intent_id"])[-1] == ("PRECALL_PUSH", "ERROR")
    assert _trail(other_tenant, good["intent_id"]) == [("PRECALL_PUSH", "SKIPPED"), ("PSTN", "SENT")]


# 4. Waiting or exhausted intents do not starve due ones


def test_no_starvation_by_waiting_intents(client: TestClient, tenant: SeededTenant, clock: Clock) -> None:
    for i in range(3):
        _create(client, tenant, to={"phone": f"+96650000030{i}"}, channel_preference=["PRECALL_PUSH"])
    newcomer = _create(client, tenant, to={"phone": "+966500000399"}, channel_preference=["PSTN"])
    for k in range(4):
        delivery.deliver_due(
            clock.now + OPENS + timedelta(seconds=k), MockRecipientDirectory(), MockPushSender(), batch=2
        )
    assert _trail(tenant, newcomer["intent_id"]) == [("PSTN", "SENT")]


# 5. SDK resolve before window opens does not deliver early


def test_early_sdk_resolve_delivers_when_window_opens(
    client: TestClient, tenant: SeededTenant, clock: Clock
) -> None:
    created = _create(client, tenant)
    device = uuid4()
    with anonymous_tx() as conn:
        early = delivery.deliver_via_sdk(conn, created["context_token"], device, clock.now)
    assert early.status.value == "REQUESTED"
    delivery.deliver_due(clock.now + OPENS, MockRecipientDirectory(), MockPushSender())
    r = client.get(f"/v1/intents/{created['intent_id']}", headers=auth(token_for(client, tenant))).json()
    assert r["status"] == "DELIVERED" and r["channel_used"] == "SDK"


# 6. Invalid requests consume no quota


def test_invalid_request_consumes_no_quota(client: TestClient, tenant: SeededTenant) -> None:
    from app.core.config import settings

    settings.recipient_intents_per_tenant_per_day = 1
    tok = token_for(client, tenant)
    bad = client.post(
        "/v1/intents", json=intent_body(tenant, purpose_code="NOT.A.CODE"), headers=auth(tok, str(uuid4()))
    )
    assert bad.status_code == 422
    ok = client.post("/v1/intents", json=intent_body(tenant), headers=auth(tok, str(uuid4())))
    assert ok.status_code == 201


def test_urgent_fails_closed_when_limiter_down(client: TestClient, tenant: SeededTenant) -> None:
    ratelimit.set_limiter(
        ratelimit.Limiter(redis.Redis.from_url("redis://127.0.0.1:1/0", socket_timeout=0.2))
    )
    r = client.post(
        "/v1/intents",
        json=intent_body(
            tenant, purpose_code="CARD.TRANSACTION.VERIFY", priority="URGENT", expected_duration_min=3
        ),
        headers=auth(token_for(client, tenant), str(uuid4())),
    )
    assert r.status_code == 503 and r.json()["code"] == "rate_limit_unavailable"


# 7. Webhooks: per endpoint cap, fresh signature time


def test_per_endpoint_cap_and_fresh_signature(client: TestClient, tenant: SeededTenant, clock: Clock) -> None:
    with tenant_tx(tenant.tenant_id) as conn:
        webhooks.create_endpoint(conn, tenant.tenant_id, "https://hooks.slow.example/x", ["intent.delivered"])
    for i in range(5):
        _create(client, tenant, to={"phone": f"+96650000050{i}"}, channel_preference=["PSTN"])
    delivery.deliver_due(clock.now + OPENS, MockRecipientDirectory(), MockPushSender())
    sender = MockHttpSender()
    webhooks.dispatch_due(clock.now + OPENS, sender, per_endpoint=2)
    mine = [r for r in sender.requests if r[0] == "https://hooks.slow.example/x"]
    assert len(mine) == 2
    ts = int(mine[0][2]["RingSays-Signature"].split(",")[0][2:])
    assert abs(ts - time.time()) < 60, "signed with send time, not tick time"
    with worker_tx() as conn:
        pending = conn.execute(
            select(webhook_deliveries.c.id).where(
                webhook_deliveries.c.status == "PENDING", webhook_deliveries.c.tenant_id == tenant.tenant_id
            )
        ).all()
    assert len(pending) == 3


# 8. SSRF address forms


@pytest.mark.parametrize(
    "addr,public",
    [
        ("8.8.8.8", True),
        ("10.0.0.1", False),
        ("::ffff:10.0.0.1", False),
        ("::127.0.0.1", False),
        ("64:ff9b::a00:1", False),
        ("64:ff9b::808:808", True),
        ("2002:0a00:0001::1", False),  # 6to4 wrapping 10.0.0.1
        ("fe80::1%eth0", False),
        ("2001:4860:4860::8888", True),
    ],
)
def test_public_address_check(addr: str, public: bool) -> None:
    assert webhooks.is_public_address(addr) is public


def test_non_443_port_rejected(tenant: SeededTenant) -> None:
    from app.modules.intent.errors import RuleViolation

    with pytest.raises(RuleViolation, match="443"), tenant_tx(tenant.tenant_id) as conn:
        webhooks.create_endpoint(
            conn, tenant.tenant_id, "https://hooks.example.com:8443/x", ["intent.accepted"]
        )
