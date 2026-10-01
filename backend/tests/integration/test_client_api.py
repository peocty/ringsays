"""RingSays app (client API) end to end: sign in, delivery to a real account, inbox, responses, privacy."""

from __future__ import annotations

from datetime import timedelta
from typing import Any
from uuid import UUID, uuid4

import pytest
import redis
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.core.db import tenant_tx, user_tx
from app.core.phone import phone_hash
from app.core.tables import delivery_attempts, webhook_deliveries
from app.modules.client.directory import DbRecipientDirectory
from app.modules.delivery import service as delivery
from app.modules.delivery.adapters import MockPushSender
from app.modules.identity import service as identity
from app.modules.webhooks import service as webhooks
from app.platform import ratelimit
from app.scripts.seed import SeededTenant

from .conftest import AppUser, Clock, auth, intent_body, token_for

pytestmark = pytest.mark.integration
OPENS = timedelta(minutes=30)


def _phone() -> str:
    return f"+9665{uuid4().int % 10**8:08d}"


def _bank_intent(client: TestClient, t: SeededTenant, phone: str, **overrides: Any) -> str:
    body = intent_body(t, to={"phone": phone}, channel_preference=["PRECALL_PUSH", "PSTN"], **overrides)
    r = client.post("/v1/intents", json=body, headers=auth(token_for(client, t), str(uuid4())))
    assert r.status_code == 201, r.text
    return str(r.json()["intent_id"])


def _deliver(clock: Clock, push: MockPushSender | None = None) -> MockPushSender:
    push = push or MockPushSender()
    delivery.deliver_due(clock.now + OPENS, DbRecipientDirectory(), push)
    return push


def _trail(t: SeededTenant, intent_id: str) -> list[tuple[str, str]]:
    with tenant_tx(t.tenant_id) as conn:
        rows = conn.execute(
            select(delivery_attempts.c.channel, delivery_attempts.c.outcome)
            .where(delivery_attempts.c.intent_id == UUID(intent_id))
            .order_by(delivery_attempts.c.id)
        ).all()
    return [(r.channel, r.outcome) for r in rows]


# Sign in


def test_sign_in_wrong_code_attempts_and_same_user(client: TestClient) -> None:
    phone = _phone()
    r = client.post("/v1/auth/otp", json={"phone": phone})
    challenge = r.json()["challenge_id"]
    for _ in range(identity.OTP_MAX_ATTEMPTS):
        bad = client.post(
            "/v1/auth/verify",
            json={
                "challenge_id": challenge,
                "code": "000000",
                "device": {"platform": "IOS", "public_key": "A" * 120, "app_version": "1"},
            },
        )
        assert bad.status_code in (400, 401, 422)
    first = AppUser(client, phone)
    second = AppUser(client, phone)
    assert first.tokens["user_id"] == second.tokens["user_id"]
    assert first.tokens["device_id"] != second.tokens["device_id"]


def test_code_attempt_limit(client: TestClient) -> None:
    phone = _phone()
    user = AppUser(client, phone)  # creates key material we can reuse
    r = client.post("/v1/auth/otp", json={"phone": phone})
    challenge = r.json()["challenge_id"]
    code = identity.get_sms().last_code_for(phone)  # type: ignore[attr-defined]
    device = {"platform": "IOS", "public_key": _public(user), "app_version": "1"}
    for _ in range(identity.OTP_MAX_ATTEMPTS):
        client.post(
            "/v1/auth/verify",
            json={
                "challenge_id": challenge,
                "code": "123456" if code != "123456" else "654321",
                "device": device,
            },
        )
    late = client.post("/v1/auth/verify", json={"challenge_id": challenge, "code": code, "device": device})
    assert late.status_code == 401, "correct code refused after too many attempts"


def _public(user: AppUser) -> str:
    import base64

    from cryptography.hazmat.primitives import serialization

    return base64.b64encode(
        user.key.public_key().public_bytes(
            serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo
        )
    ).decode()


def test_otp_rate_limit_and_fail_closed(client: TestClient) -> None:
    phone = _phone()
    codes = [client.post("/v1/auth/otp", json={"phone": phone}).status_code for _ in range(6)]
    assert codes == [202] * 5 + [429]
    ratelimit.set_limiter(
        ratelimit.Limiter(redis.Redis.from_url("redis://127.0.0.1:1/0", socket_timeout=0.2))
    )
    assert client.post("/v1/auth/otp", json={"phone": _phone()}).status_code == 503


def test_sms_text_contains_no_other_data(client: TestClient) -> None:
    phone = _phone()
    client.post("/v1/auth/otp", json={"phone": phone, "locale": "en"})
    msg = identity.get_sms().sent[-1][1]  # type: ignore[attr-defined]
    assert "RingSays code" in msg and "Never share" in msg


def test_refresh_rotation_and_reuse_detection(client: TestClient) -> None:
    user = AppUser(client, _phone())
    old = user.tokens["refresh_token"]
    r = client.post("/v1/auth/refresh", json={"refresh_token": old, "device_signature": user.sign(old)})
    assert r.status_code == 200
    new = r.json()["refresh_token"]
    assert new != old
    bad_sig = client.post(
        "/v1/auth/refresh", json={"refresh_token": new, "device_signature": user.sign("other")}
    )
    assert bad_sig.status_code == 401
    reuse = client.post("/v1/auth/refresh", json={"refresh_token": old, "device_signature": user.sign(old)})
    assert reuse.status_code == 401
    after = client.post("/v1/auth/refresh", json={"refresh_token": new, "device_signature": user.sign(new)})
    assert after.status_code == 401, "reuse of old token revoked whole family"


def test_access_token_required(client: TestClient) -> None:
    assert client.get("/v1/inbox").status_code == 401
    assert client.get("/v1/inbox", headers={"Authorization": "Bearer junk"}).status_code == 401


def test_enterprise_token_rejected_on_client_api(client: TestClient, tenant: SeededTenant) -> None:
    r = client.get("/v1/inbox", headers=auth(token_for(client, tenant)))
    assert r.status_code == 401


# Delivery to a real account, inbox, respond, webhooks


def test_end_to_end_push_inbox_respond_webhook(
    client: TestClient, tenant: SeededTenant, clock: Clock
) -> None:
    with tenant_tx(tenant.tenant_id) as conn:
        webhooks.create_endpoint(
            conn, tenant.tenant_id, "https://hooks.mockbank.example/e2e", ["intent.scheduled"]
        )
    phone = _phone()
    user = AppUser(client, phone)
    intent_id = _bank_intent(client, tenant, phone)
    first = client.get("/v1/inbox", headers=user.headers)
    assert first.status_code == 200, first.text
    assert first.json()["items"] == [], "nothing before delivery"
    push = _deliver(clock)
    assert [(str(d), k, str(i)) for d, k, i in push.sent] == [(user.tokens["device_id"], "INTENT", intent_id)]
    items = client.get("/v1/inbox", headers=user.headers).json()["items"]
    assert len(items) == 1
    item = items[0]
    assert item["why"] == "توضيح بخصوص مستندات التمويل العقاري"
    assert item["organisation_name"] == "بنك تجريبي"
    assert item["agent_display_name"] == "موظف تجريبي"
    assert item["verification_level"] == "ORG_AGENT_NUMBER"
    assert item["actions"] == ["TALK_NOW", "LATER", "PROPOSE", "MESSAGE", "DECLINE"]
    en = client.get(f"/v1/me/intents/{intent_id}", headers={**user.headers, "Accept-Language": "en"}).json()
    assert en["why"] == "Home finance document clarification"
    clock.now += OPENS
    r = client.post(
        f"/v1/intents/{intent_id}/respond",
        headers=user.headers,
        json={"action": "LATER", "later_minutes": 30},
    )
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "SCHEDULED" and r.json()["actions"] == ["PROPOSE", "DECLINE"]
    folder = client.get("/v1/inbox?folder=SCHEDULED", headers=user.headers).json()["items"]
    assert [i["intent_id"] for i in folder] == [intent_id]
    with tenant_tx(tenant.tenant_id) as conn:
        types = (
            conn.execute(
                select(webhook_deliveries.c.event_type).where(
                    webhook_deliveries.c.intent_id == UUID(intent_id)
                )
            )
            .scalars()
            .all()
        )
    assert types == ["intent.scheduled"]
    bank = client.get(f"/v1/intents/{intent_id}", headers=auth(token_for(client, tenant))).json()
    assert bank["timeline"][-1]["actor"] == "RECEIVER"


def test_other_user_cannot_see_or_respond(client: TestClient, tenant: SeededTenant, clock: Clock) -> None:
    owner, stranger = AppUser(client, _phone()), AppUser(client, _phone())
    intent_id = _bank_intent(client, tenant, owner.phone)
    _deliver(clock)
    assert client.get("/v1/inbox", headers=stranger.headers).json()["items"] == []
    assert client.get(f"/v1/me/intents/{intent_id}", headers=stranger.headers).status_code == 404
    r = client.post(f"/v1/intents/{intent_id}/respond", headers=stranger.headers, json={"action": "ACCEPT"})
    assert r.status_code == 404


def test_pstn_fallback_not_in_app_inbox(client: TestClient, tenant: SeededTenant, clock: Clock) -> None:
    user = AppUser(client, _phone(), apns=None)  # no push token: app cannot be reached
    intent_id = _bank_intent(client, tenant, user.phone)
    _deliver(clock)
    assert _trail(tenant, intent_id) == [("PRECALL_PUSH", "SKIPPED"), ("PSTN", "SENT")]
    assert client.get("/v1/inbox", headers=user.headers).json()["items"] == []


# Consents


def test_consent_ledger_and_withdrawal_blocks_app_even_urgent(
    client: TestClient, tenant: SeededTenant, clock: Clock
) -> None:
    user = AppUser(client, _phone())
    _bank_intent(client, tenant, user.phone)
    _deliver(clock)
    consents = client.get("/v1/me/consents", headers=user.headers).json()["items"]
    assert (
        len(consents) == 1 and consents[0]["grantee"] == "بنك تجريبي" and consents[0]["withdrawn_at"] is None
    )
    assert (
        client.delete(f"/v1/me/consents/{consents[0]['consent_id']}", headers=user.headers).status_code == 204
    )
    urgent = _bank_intent(
        client,
        tenant,
        user.phone,
        purpose_code="CARD.TRANSACTION.VERIFY",
        priority="URGENT",
        expected_duration_min=3,
    )
    push = _deliver(clock)
    assert push.sent == []
    assert _trail(tenant, urgent) == [("PRECALL_PUSH", "SKIPPED"), ("PSTN", "SENT")]


# Preferences


def test_preferences_etag_validation_and_effect(
    client: TestClient, tenant: SeededTenant, clock: Clock
) -> None:
    user = AppUser(client, _phone())
    r = client.get("/v1/me/preferences", headers=user.headers)
    assert r.headers["ETag"] == 'W/"v0"' and r.json()["timezone"] == "Asia/Riyadh"
    quiet = {
        "timezone": "Asia/Riyadh",
        "verified_businesses_only": True,
        "night_mode": {
            "days": ["MON", "TUE", "WED", "THU", "FRI", "SAT", "SUN"],
            "start": "10:00",
            "end": "11:00",
        },
        "rules": [],
    }
    ok = client.put("/v1/me/preferences", json=quiet, headers={**user.headers, "If-Match": 'W/"v0"'})
    assert ok.status_code == 200 and ok.headers["ETag"] == 'W/"v1"'
    stale = client.put("/v1/me/preferences", json=quiet, headers={**user.headers, "If-Match": 'W/"v0"'})
    assert stale.status_code == 412
    bad = client.put(
        "/v1/me/preferences",
        json={**quiet, "timezone": "Asia/Riyad"},
        headers={**user.headers, "If-Match": 'W/"v1"'},
    )
    assert bad.status_code == 400
    intent_id = _bank_intent(client, tenant, user.phone, valid_until="2026-10-04T09:00:00Z")
    _deliver(clock)  # 07:30 UTC = 10:30 Riyadh, inside quiet window
    assert _trail(tenant, intent_id) == [("PRECALL_PUSH", "HELD")]


# Export and erasure


def test_export_then_erase(client: TestClient, tenant: SeededTenant, clock: Clock) -> None:
    user = AppUser(client, _phone())
    _bank_intent(client, tenant, user.phone)
    _deliver(clock)
    data = client.post("/v1/me/export", headers=user.headers).json()
    assert data["profile"]["phone"] == user.phone
    assert data["address_book"] == "never collected"
    assert len(data["communications_received"]) == 1 and len(data["consents"]) == 1
    assert "public_key" not in str(data["devices"])
    assert client.delete("/v1/me", headers=user.headers).status_code == 202
    assert client.get("/v1/inbox", headers=user.headers).status_code == 401
    old = user.tokens["refresh_token"]
    assert (
        client.post(
            "/v1/auth/refresh", json={"refresh_token": old, "device_signature": user.sign(old)}
        ).status_code
        == 401
    )
    later = _bank_intent(client, tenant, user.phone)
    push = _deliver(clock)
    assert push.sent == [] and _trail(tenant, later)[-1] == ("PSTN", "SENT"), "erased user is not reachable"
    fresh = AppUser(client, user.phone)
    assert fresh.tokens["user_id"] != user.tokens["user_id"], "signing in again starts a new account"


# Enterprise SDK through Context Token


def test_sdk_token_endpoints(client: TestClient, tenant: SeededTenant, clock: Clock) -> None:
    r = client.post(
        "/v1/intents", json=intent_body(tenant), headers=auth(token_for(client, tenant), str(uuid4()))
    )
    token = r.json()["context_token"]
    device = str(uuid4())
    clock.now += OPENS
    shown = client.get(f"/v1/tokens/{token}", headers={"RingSays-Device-Id": device, "Accept-Language": "en"})
    assert shown.status_code == 200 and shown.json()["status"] == "DELIVERED"
    assert shown.json()["organisation_name"] == "Mock Bank (demo tenant)"
    other = client.get(f"/v1/tokens/{token}", headers={"RingSays-Device-Id": str(uuid4())})
    assert other.status_code == 403
    done = client.post(
        f"/v1/tokens/{token}/respond",
        headers={"RingSays-Device-Id": device},
        json={"action": "DECLINE", "decline_reason": "ALREADY_RESOLVED"},
    )
    assert done.status_code == 200 and done.json()["status"] == "DECLINED"
    assert client.get(f"/v1/tokens/{token}", headers={"RingSays-Device-Id": device}).status_code == 410


def test_push_token_update_own_device_only(client: TestClient) -> None:
    a, b = AppUser(client, _phone()), AppUser(client, _phone())
    ok = client.put(
        f"/v1/devices/{a.tokens['device_id']}/push-tokens", headers=a.headers, json={"apns": "new"}
    )
    assert ok.status_code == 204
    foreign = client.put(
        f"/v1/devices/{b.tokens['device_id']}/push-tokens", headers=a.headers, json={"apns": "x"}
    )
    assert foreign.status_code == 404


# Isolation between users


def test_identity_rows_isolated_between_users(client: TestClient) -> None:
    from app.core.tables import consents, devices, preferences, refresh_tokens, users

    a, b = AppUser(client, _phone()), AppUser(client, _phone())
    with user_tx(UUID(b.tokens["user_id"]), phone_hash(b.phone), None) as conn:
        for table, col in [
            (users, users.c.id),
            (devices, devices.c.user_id),
            (refresh_tokens, refresh_tokens.c.user_id),
            (preferences, preferences.c.user_id),
            (consents, consents.c.user_id),
        ]:
            rows = conn.execute(select(table).where(col == UUID(a.tokens["user_id"]))).all()
            assert rows == [], table.fullname
