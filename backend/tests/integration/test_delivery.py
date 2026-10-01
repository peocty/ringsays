"""Delivery ladder, Context Tokens, receiver rules and pre call push, end to end on PostgreSQL."""

from __future__ import annotations

from datetime import time, timedelta
from typing import Any
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.core.db import anonymous_tx, tenant_tx
from app.core.tables import delivery_attempts
from app.modules.context.service import TokenGone, TokenRefused
from app.modules.delivery import adapters
from app.modules.delivery import service as delivery
from app.modules.delivery.adapters import MockPushSender, MockRecipientDirectory, PushTarget, Recipient
from app.modules.preference.engine import DAYS, Preferences, TimeWindow
from app.scripts.seed import SeededTenant

from .conftest import Clock, auth, intent_body, token_for

pytestmark = pytest.mark.integration

PHONE = "+966500000001"
OPENS = timedelta(minutes=30)  # intent_body valid_from is 07:30 UTC, clock is 07:00 UTC


def _create(client: TestClient, t: SeededTenant, **overrides: Any) -> dict[str, Any]:
    r = client.post(
        "/v1/intents", json=intent_body(t, **overrides), headers=auth(token_for(client, t), str(uuid4()))
    )
    assert r.status_code == 201, r.text
    return dict(r.json())


def _get(client: TestClient, t: SeededTenant, intent_id: str) -> dict[str, Any]:
    return dict(client.get(f"/v1/intents/{intent_id}", headers=auth(token_for(client, t))).json())


def _trail(t: SeededTenant, intent_id: str) -> list[tuple[str, str]]:
    with tenant_tx(t.tenant_id) as conn:
        rows = conn.execute(
            select(delivery_attempts.c.channel, delivery_attempts.c.outcome)
            .where(delivery_attempts.c.intent_id == UUID(intent_id))
            .order_by(delivery_attempts.c.id)
        ).all()
    return [(r.channel, r.outcome) for r in rows]


def _recipient(night: TimeWindow | None = None, devices: int = 1) -> Recipient:
    return Recipient(
        user_ref=uuid4(),
        devices=tuple(PushTarget(uuid4(), "IOS", f"apns-token-{i}") for i in range(devices)),
        preferences=Preferences(night_mode=night),
    )


def test_nothing_delivered_before_window_opens(
    client: TestClient, tenant: SeededTenant, clock: Clock
) -> None:
    created = _create(client, tenant)
    delivery.deliver_due(clock.now, MockRecipientDirectory(), MockPushSender())
    assert _trail(tenant, created["intent_id"]) == []
    assert _get(client, tenant, created["intent_id"])["status"] == "REQUESTED"


def test_sdk_token_resolve_delivers_once_and_binds_device(
    client: TestClient, tenant: SeededTenant, clock: Clock
) -> None:
    created = _create(client, tenant)
    token = created["context_token"]
    assert token and len(token) >= 22
    device, other = uuid4(), uuid4()
    with anonymous_tx() as conn:
        intent = delivery.deliver_via_sdk(conn, token, device, clock.now + OPENS)
    assert intent.status.value == "DELIVERED" and intent.channel_used.value == "SDK"  # type: ignore[union-attr]
    with anonymous_tx() as conn:  # same device again: allowed, no second delivery
        again = delivery.deliver_via_sdk(conn, token, device, clock.now + OPENS)
    assert again.status.value == "DELIVERED"
    with pytest.raises(TokenRefused), anonymous_tx() as conn:
        delivery.deliver_via_sdk(conn, token, other, clock.now + OPENS)
    with pytest.raises(TokenRefused), anonymous_tx() as conn:
        delivery.deliver_via_sdk(conn, "not-a-real-token-at-all", device, clock.now)


def test_token_revoked_when_intent_ends(client: TestClient, tenant: SeededTenant, clock: Clock) -> None:
    created = _create(client, tenant)
    tok = token_for(client, tenant)
    client.post(f"/v1/intents/{created['intent_id']}/cancel", headers=auth(tok, str(uuid4())))
    with pytest.raises(TokenGone), anonymous_tx() as conn:
        delivery.deliver_via_sdk(conn, created["context_token"], uuid4(), clock.now + OPENS)


def test_token_expires_with_intent(client: TestClient, tenant: SeededTenant, clock: Clock) -> None:
    created = _create(client, tenant)
    with pytest.raises(TokenGone), anonymous_tx() as conn:
        delivery.deliver_via_sdk(conn, created["context_token"], uuid4(), clock.now + timedelta(hours=2))


def test_push_to_ringsays_device(client: TestClient, tenant: SeededTenant, clock: Clock) -> None:
    created = _create(client, tenant, channel_preference=["PRECALL_PUSH", "PSTN"])
    assert created["context_token"] is None, "no SDK channel, no token"
    directory = MockRecipientDirectory({PHONE: _recipient(devices=2)})
    push = MockPushSender()
    delivery.deliver_due(clock.now + OPENS, directory, push)
    got = _get(client, tenant, created["intent_id"])
    assert got["status"] == "DELIVERED" and got["channel_used"] == "PRECALL_PUSH"
    assert [(k, str(i)) for _, k, i in push.sent] == [("INTENT", created["intent_id"])] * 2
    assert _trail(tenant, created["intent_id"]) == [("PRECALL_PUSH", "SENT")]


def test_no_app_falls_back_to_pstn_then_direct_call(
    client: TestClient, tenant: SeededTenant, clock: Clock
) -> None:
    created = _create(client, tenant, channel_preference=["PRECALL_PUSH", "PSTN"])
    delivery.deliver_due(clock.now + OPENS, MockRecipientDirectory(), MockPushSender())
    got = _get(client, tenant, created["intent_id"])
    assert got["channel_used"] == "PSTN"
    assert _trail(tenant, created["intent_id"]) == [("PRECALL_PUSH", "SKIPPED"), ("PSTN", "SENT")]
    clock.now += OPENS
    r = client.post(
        f"/v1/intents/{created['intent_id']}/calling", headers=auth(token_for(client, tenant), str(uuid4()))
    )
    assert r.status_code == 202 and r.json()["status"] == "IN_PROGRESS"


def test_rejected_push_moves_down_ladder(client: TestClient, tenant: SeededTenant, clock: Clock) -> None:
    created = _create(client, tenant, channel_preference=["PRECALL_PUSH", "PSTN"])
    r = _recipient()
    push = MockPushSender(fail_devices={r.devices[0].device_id})
    delivery.deliver_due(clock.now + OPENS, MockRecipientDirectory({PHONE: r}), push)
    assert _trail(tenant, created["intent_id"]) == [("PRECALL_PUSH", "FAILED")]
    delivery.deliver_due(clock.now + OPENS, MockRecipientDirectory({PHONE: r}), push)  # next tick
    assert _trail(tenant, created["intent_id"]) == [("PRECALL_PUSH", "FAILED"), ("PSTN", "SENT")]


def test_sdk_grace_then_push(client: TestClient, tenant: SeededTenant, clock: Clock) -> None:
    created = _create(client, tenant)  # SDK, PRECALL_PUSH, PSTN
    directory, push = MockRecipientDirectory({PHONE: _recipient()}), MockPushSender()
    t0 = clock.now + OPENS
    delivery.deliver_due(t0, directory, push)
    delivery.deliver_due(t0 + timedelta(minutes=1), directory, push)
    assert _get(client, tenant, created["intent_id"])["status"] == "REQUESTED", "within SDK grace"
    delivery.deliver_due(t0 + delivery.SDK_GRACE, directory, push)
    assert _get(client, tenant, created["intent_id"])["channel_used"] == "PRECALL_PUSH"
    assert _trail(tenant, created["intent_id"]) == [
        ("SDK", "SENT"),
        ("SDK", "FAILED"),
        ("PRECALL_PUSH", "SENT"),
    ]


NIGHT_UTC = TimeWindow(days=frozenset(DAYS), start=time(10, 0), end=time(11, 0))  # Riyadh 10:00 to 11:00


def test_receiver_rules_hold_then_deliver(client: TestClient, tenant: SeededTenant, clock: Clock) -> None:
    # Clock 07:00 UTC = 10:00 Riyadh. Intent window 07:30 to 08:00 UTC; quiet window ends 11:00 Riyadh.
    created = _create(
        client,
        tenant,
        channel_preference=["PRECALL_PUSH", "PSTN"],
        valid_until="2026-10-04T09:00:00Z",
    )
    directory, push = MockRecipientDirectory({PHONE: _recipient(night=NIGHT_UTC)}), MockPushSender()
    delivery.deliver_due(clock.now + OPENS, directory, push)
    delivery.deliver_due(clock.now + timedelta(minutes=45), directory, push)
    assert push.sent == [], "held during quiet hours"
    assert _trail(tenant, created["intent_id"]) == [("PRECALL_PUSH", "HELD")]
    delivery.deliver_due(clock.now + timedelta(hours=1), directory, push)
    assert _get(client, tenant, created["intent_id"])["channel_used"] == "PRECALL_PUSH"


def test_hold_past_validity_skips_app_and_falls_back(
    client: TestClient, tenant: SeededTenant, clock: Clock
) -> None:
    created = _create(client, tenant, channel_preference=["PRECALL_PUSH", "PSTN"])  # ends 08:00 UTC
    directory, push = MockRecipientDirectory({PHONE: _recipient(night=NIGHT_UTC)}), MockPushSender()
    delivery.deliver_due(clock.now + OPENS, directory, push)
    assert push.sent == [], "receiver is not woken when quiet hours outlast the intent"
    assert _trail(tenant, created["intent_id"]) == [("PRECALL_PUSH", "SKIPPED"), ("PSTN", "SENT")]


def test_urgent_verified_passes_quiet_hours(client: TestClient, tenant: SeededTenant, clock: Clock) -> None:
    created = _create(
        client,
        tenant,
        purpose_code="CARD.TRANSACTION.VERIFY",
        priority="URGENT",
        expected_duration_min=3,
        channel_preference=["PRECALL_PUSH", "PSTN"],
    )
    directory, push = MockRecipientDirectory({PHONE: _recipient(night=NIGHT_UTC)}), MockPushSender()
    delivery.deliver_due(clock.now + OPENS, directory, push)
    assert _get(client, tenant, created["intent_id"])["channel_used"] == "PRECALL_PUSH"


def test_calling_sends_precall_push(client: TestClient, tenant: SeededTenant, clock: Clock) -> None:
    created = _create(client, tenant, channel_preference=["PRECALL_PUSH", "PSTN"])
    recipient = _recipient()
    push = MockPushSender()
    adapters.configure(MockRecipientDirectory({PHONE: recipient}), push)
    delivery.deliver_due(clock.now + OPENS, adapters.get_directory(), push)
    from app.modules.intent import service as intent_service
    from app.modules.intent import state_machine as sm
    from app.modules.intent.domain import ResponseAction

    with tenant_tx(tenant.tenant_id) as conn:
        intent_service.apply_receiver(
            conn,
            UUID(created["intent_id"]),
            lambda i: sm.respond(i, ResponseAction.ACCEPT, clock.now + OPENS),
            "user:test",
            clock.now + OPENS,
        )
    clock.now += OPENS
    r = client.post(
        f"/v1/intents/{created['intent_id']}/calling", headers=auth(token_for(client, tenant), str(uuid4()))
    )
    assert r.status_code == 202
    assert [k for _, k, _ in push.sent] == ["INTENT", "PRECALL"]
