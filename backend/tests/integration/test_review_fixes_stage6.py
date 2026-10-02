"""Stage 6 review: server side sign out (no pushes to a phone after its user signed out)."""

from __future__ import annotations

from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.core.db import user_tx
from app.core.phone import phone_hash
from app.core.tables import devices, refresh_tokens

from .conftest import AppUser

pytestmark = pytest.mark.integration


def _phone() -> str:
    return f"+9665{uuid4().int % 10**8:08d}"


def test_logout_revokes_device_tokens_and_push(client: TestClient) -> None:
    phone = _phone()
    a = AppUser(client, phone)
    other = AppUser(client, phone)  # same person, second phone
    assert other.tokens["device_id"] != a.tokens["device_id"]

    assert client.post("/v1/auth/logout", headers=a.headers).status_code == 204
    assert client.get("/v1/inbox", headers=a.headers).status_code == 401
    old = a.tokens["refresh_token"]
    r = client.post("/v1/auth/refresh", json={"refresh_token": old, "device_signature": a.sign(old)})
    assert r.status_code == 401
    assert client.post("/v1/auth/logout", headers=a.headers).status_code == 401

    with user_tx(UUID(a.tokens["user_id"]), phone_hash(phone), None) as conn:
        dev = conn.execute(select(devices).where(devices.c.id == UUID(a.tokens["device_id"]))).one()
        live = conn.execute(
            select(refresh_tokens.c.token_hash).where(
                refresh_tokens.c.device_id == UUID(a.tokens["device_id"]),
                refresh_tokens.c.revoked_at.is_(None),
            )
        ).all()
    assert dev.revoked_at is not None and dev.apns_token is None and dev.fcm_token is None
    assert live == []
    # The person's other phone stays signed in.
    assert client.get("/v1/inbox", headers=other.headers).status_code == 200


def test_logout_requires_access_token(client: TestClient) -> None:
    assert client.post("/v1/auth/logout").status_code == 401


def test_sms_provider_failure_is_503_without_details(client: TestClient) -> None:
    from app.modules.identity import service as identity
    from app.platform.providers.sms import SmsSendFailed

    class Down:
        def send(self, phone: str, message: str) -> None:
            raise SmsSendFailed("taqnyat refused: HTTP 400")

    previous = identity.get_sms()
    identity.configure_sms(Down())
    try:
        r = client.post("/v1/auth/otp", json={"phone": _phone(), "locale": "ar"})
    finally:
        identity.configure_sms(previous)
    assert r.status_code == 503
    assert r.json()["code"] == "sms_unavailable"
    assert "taqnyat" not in r.text
