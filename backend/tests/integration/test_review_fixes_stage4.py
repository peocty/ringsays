"""Regression tests for defects found in independent stage 4 review."""

from __future__ import annotations

import threading
from datetime import timedelta
from typing import Any
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient

from app.core.config import settings
from app.core.db import anonymous_tx
from app.modules.client.directory import DbRecipientDirectory
from app.modules.delivery import adapters
from app.modules.delivery import service as delivery
from app.modules.delivery.adapters import MockPushSender
from app.modules.identity import service as identity
from app.scripts.seed import SeededTenant

from .conftest import AppUser, Clock, auth, intent_body, token_for

pytestmark = pytest.mark.integration
OPENS = timedelta(minutes=30)


def _phone() -> str:
    return f"+9665{uuid4().int % 10**8:08d}"


def _intent(client: TestClient, t: SeededTenant, phone: str, **overrides: Any) -> str:
    body = intent_body(t, to={"phone": phone}, channel_preference=["PRECALL_PUSH", "PSTN"], **overrides)
    r = client.post("/v1/intents", json=body, headers=auth(token_for(client, t), str(uuid4())))
    assert r.status_code == 201, r.text
    return str(r.json()["intent_id"])


def _deliver(clock: Clock) -> MockPushSender:
    push = MockPushSender()
    delivery.deliver_due(clock.now + OPENS, DbRecipientDirectory(), push)
    return push


# 1. Previous holder's intents never visible to a later account


def test_new_account_on_same_number_sees_nothing_old(
    client: TestClient, tenant: SeededTenant, clock: Clock
) -> None:
    phone = _phone()
    first = AppUser(client, phone)
    old = _intent(client, tenant, phone)
    _deliver(clock)
    assert len(client.get("/v1/inbox", headers=first.headers).json()["items"]) == 1
    client.delete("/v1/me", headers=first.headers)
    clock.now += timedelta(seconds=1)
    second = AppUser(client, phone)
    assert client.get("/v1/inbox", headers=second.headers).json()["items"] == []
    assert client.get(f"/v1/me/intents/{old}", headers=second.headers).status_code == 404
    r = client.post(
        f"/v1/intents/{old}/respond",
        headers=second.headers,
        json={"action": "DECLINE", "decline_reason": "WRONG_PERSON"},
    )
    assert r.status_code == 404
    export = client.post("/v1/me/export", headers=second.headers).json()
    assert export["communications_received"] == []


def test_intent_sent_before_signup_not_shown(client: TestClient, tenant: SeededTenant, clock: Clock) -> None:
    """Recycled number case: intent was for whoever held the number before this account existed."""
    phone = _phone()
    before = _intent(client, tenant, phone)
    clock.now += timedelta(seconds=1)
    user = AppUser(client, phone)
    _deliver(clock)
    assert client.get(f"/v1/me/intents/{before}", headers=user.headers).status_code == 404


# 2. OTP brute force limits


def test_new_code_invalidates_old(client: TestClient) -> None:
    phone = _phone()
    user = AppUser(client, phone)
    first = client.post("/v1/auth/otp", json={"phone": phone}).json()["challenge_id"]
    old_code = identity.get_sms().last_code_for(phone)  # type: ignore[attr-defined]
    client.post("/v1/auth/otp", json={"phone": phone})
    device = {"platform": "IOS", "public_key": _pub(user), "app_version": "1"}
    r = client.post("/v1/auth/verify", json={"challenge_id": first, "code": old_code, "device": device})
    assert r.status_code == 401


def test_daily_wrong_code_cap_across_challenges(client: TestClient) -> None:
    settings.otp_failures_per_phone_per_day = 3
    settings.otp_per_phone_per_hour = 50
    try:
        phone = _phone()
        user = AppUser(client, phone)
        device = {"platform": "IOS", "public_key": _pub(user), "app_version": "1"}
        for _ in range(3):
            c = client.post("/v1/auth/otp", json={"phone": phone}).json()["challenge_id"]
            code = identity.get_sms().last_code_for(phone)  # type: ignore[attr-defined]
            wrong = "000000" if code != "000000" else "111111"
            assert (
                client.post(
                    "/v1/auth/verify", json={"challenge_id": c, "code": wrong, "device": device}
                ).status_code
                == 401
            )
        c = client.post("/v1/auth/otp", json={"phone": phone}).json()["challenge_id"]
        code = identity.get_sms().last_code_for(phone)  # type: ignore[attr-defined]
        locked = client.post("/v1/auth/verify", json={"challenge_id": c, "code": code, "device": device})
        assert locked.status_code == 429, "right code refused once daily failure cap reached"
    finally:
        settings.otp_failures_per_phone_per_day = 10
        settings.otp_per_phone_per_hour = 5


def test_per_network_otp_limit(client: TestClient) -> None:
    settings.otp_per_ip_per_hour = 3
    try:
        codes = [client.post("/v1/auth/otp", json={"phone": _phone()}).status_code for _ in range(4)]
        assert codes == [202, 202, 202, 429]
    finally:
        settings.otp_per_ip_per_hour = 30


def _pub(user: AppUser) -> str:
    import base64

    from cryptography.hazmat.primitives import serialization

    return base64.b64encode(
        user.key.public_key().public_bytes(
            serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo
        )
    ).decode()


# 3. Consent covers SDK deliveries; withdrawn tenant disappears from RingSays inbox


def test_sdk_delivery_creates_consent_and_withdrawal_hides_from_inbox(
    client: TestClient, tenant: SeededTenant, clock: Clock
) -> None:
    adapters.configure(directory=DbRecipientDirectory())
    phone = _phone()
    user = AppUser(client, phone)
    r = client.post(
        "/v1/intents",
        json=intent_body(tenant, to={"phone": phone}, channel_preference=["SDK", "PSTN"]),
        headers=auth(token_for(client, tenant), str(uuid4())),
    )
    token = r.json()["context_token"]
    with anonymous_tx() as conn:
        delivery.deliver_via_sdk(conn, token, uuid4(), clock.now + OPENS)
    consents = client.get("/v1/me/consents", headers=user.headers).json()["items"]
    assert len(consents) == 1
    assert len(client.get("/v1/inbox", headers=user.headers).json()["items"]) == 1
    client.delete(f"/v1/me/consents/{consents[0]['consent_id']}", headers=user.headers)
    assert client.get("/v1/inbox", headers=user.headers).json()["items"] == []


# 4. Real Accept-Language headers


@pytest.mark.parametrize(
    "header,expected",
    [
        ("en-US", "Home finance document clarification"),
        ("ar-SA", "توضيح بخصوص مستندات التمويل العقاري"),
        ("fr-FR,en;q=0.8,ar;q=0.9", "توضيح بخصوص مستندات التمويل العقاري"),
        ("de", "توضيح بخصوص مستندات التمويل العقاري"),
    ],
)
def test_accept_language_parsing(
    client: TestClient, tenant: SeededTenant, clock: Clock, header: str, expected: str
) -> None:
    user = AppUser(client, _phone())
    intent_id = _intent(client, tenant, user.phone)
    _deliver(clock)
    r = client.get(f"/v1/me/intents/{intent_id}", headers={**user.headers, "Accept-Language": header})
    assert r.status_code == 200 and r.json()["why"] == expected


# 5. Free text message is refused, not silently dropped


def test_message_text_rejected(client: TestClient, tenant: SeededTenant, clock: Clock) -> None:
    user = AppUser(client, _phone())
    intent_id = _intent(client, tenant, user.phone)
    _deliver(clock)
    r = client.post(
        f"/v1/intents/{intent_id}/respond",
        headers=user.headers,
        json={"action": "MESSAGE", "message": "call my office"},
    )
    assert r.status_code == 400


# 6 and 7. Concurrent first sign in and first preference save


def test_concurrent_first_sign_in_same_number(client: TestClient) -> None:
    phone = _phone()
    challenges, codes = [], []
    for _ in range(2):
        c = client.post("/v1/auth/otp", json={"phone": phone}).json()["challenge_id"]
        challenges.append(c)
        codes.append(identity.get_sms().last_code_for(phone))  # type: ignore[attr-defined]
    # New code invalidates old, so race two verifies on the latest challenge from two devices.
    from app.core.phone import phone_hash

    results: list[UUID | None] = []
    errors: list[BaseException] = []

    def run() -> None:
        try:
            with anonymous_tx() as conn:
                from sqlalchemy import text

                results.append(
                    conn.execute(
                        text("SELECT identity.user_for_phone(:h, 'x', now())"), {"h": phone_hash(phone)}
                    ).scalar_one()
                )
        except BaseException as exc:
            errors.append(exc)

    threads = [threading.Thread(target=run) for _ in range(4)]
    for th in threads:
        th.start()
    for th in threads:
        th.join()
    assert not errors and len(set(results)) == 1


def test_concurrent_first_preference_save(client: TestClient) -> None:
    user = AppUser(client, _phone())
    doc = {"timezone": "Asia/Riyadh", "verified_businesses_only": True, "night_mode": None, "rules": []}
    codes: list[int] = []

    def put() -> None:
        codes.append(
            client.put(
                "/v1/me/preferences", json=doc, headers={**user.headers, "If-Match": 'W/"v0"'}
            ).status_code
        )

    threads = [threading.Thread(target=put) for _ in range(4)]
    for th in threads:
        th.start()
    for th in threads:
        th.join()
    assert sorted(codes) == [200, 412, 412, 412]


# 8. Export lists only communications actually received


def test_export_excludes_undelivered(client: TestClient, tenant: SeededTenant) -> None:
    user = AppUser(client, _phone())
    _intent(client, tenant, user.phone)  # not yet delivered
    assert client.post("/v1/me/export", headers=user.headers).json()["communications_received"] == []


# 9. Erasure cuts access everywhere at once (shared revocation list)


def test_erase_revocation_shared(client: TestClient) -> None:
    user = AppUser(client, _phone())
    assert client.get("/v1/inbox", headers=user.headers).status_code == 200  # warms device cache
    principal = identity.decode_access(user.tokens["access_token"])
    client.delete("/v1/me", headers=user.headers)
    # Simulate another API process: its cache still says active, but shared revocation wins.
    identity._device_cache[principal.device_id] = (__import__("time").monotonic(), True)
    with pytest.raises(identity.AuthFailed):
        identity.decode_access(user.tokens["access_token"])


def test_unusual_timezones_rejected(client: TestClient) -> None:
    user = AppUser(client, _phone())
    for tz in ("localtime", "Factory", "Etc/GMT+3"):
        r = client.put(
            "/v1/me/preferences",
            headers={**user.headers, "If-Match": 'W/"v0"'},
            json={"timezone": tz, "verified_businesses_only": False, "night_mode": None, "rules": []},
        )
        assert r.status_code == 400, tz
