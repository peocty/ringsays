# ruff: noqa: S106  (push tokens in tests are not secrets)
"""Stage 9 review: dead push tokens are forgotten and provider errors reach the attempt trail."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import timedelta
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.core.db import tenant_tx, user_tx
from app.core.phone import phone_hash
from app.core.tables import delivery_attempts, devices
from app.modules.client.directory import DbRecipientDirectory
from app.modules.delivery import service as delivery
from app.modules.delivery.adapters import PushResult, PushTarget
from app.scripts.seed import SeededTenant

from .conftest import AppUser, Clock, auth, intent_body, token_for

pytestmark = pytest.mark.integration


@dataclass
class DeadApns:
    seen: list[str] = field(default_factory=list)

    def send(self, target: PushTarget, kind: str, intent_id: UUID) -> PushResult:
        self.seen.append(target.token)
        return PushResult(ok=False, error="apns 410 Unregistered", dead_token=True)


def test_dead_token_is_forgotten_and_reason_recorded(
    client: TestClient, tenant: SeededTenant, clock: Clock
) -> None:
    phone = f"+9665{uuid4().int % 10**8:08d}"
    user = AppUser(client, phone)
    body = intent_body(tenant, to={"phone": phone}, channel_preference=["PRECALL_PUSH", "PSTN"])
    r = client.post("/v1/intents", json=body, headers=auth(token_for(client, tenant), str(uuid4())))
    assert r.status_code == 201, r.text
    intent_id = UUID(r.json()["intent_id"])

    push = DeadApns()
    delivery.deliver_due(clock.now + timedelta(minutes=30), DbRecipientDirectory(), push)
    assert push.seen == ["apns-test-token"]

    with user_tx(UUID(user.tokens["user_id"]), phone_hash(phone), None) as conn:
        dev = conn.execute(select(devices).where(devices.c.id == UUID(user.tokens["device_id"]))).one()
    assert dev.apns_token is None and dev.revoked_at is None  # device stays; only the token goes
    with tenant_tx(tenant.tenant_id) as conn:
        reasons = (
            conn.execute(select(delivery_attempts.c.reason).where(delivery_attempts.c.intent_id == intent_id))
            .scalars()
            .all()
        )
    assert "all devices rejected push: apns 410 Unregistered" in reasons
    assert phone not in " ".join(r or "" for r in reasons)


def test_fresh_token_is_not_cleared_by_a_late_dead_report(client: TestClient) -> None:
    phone = f"+9665{uuid4().int % 10**8:08d}"
    user = AppUser(client, phone)
    device_id = UUID(user.tokens["device_id"])
    r = client.put(f"/v1/devices/{device_id}/push-tokens", headers=user.headers, json={"apns": "fresh-token"})
    assert r.status_code == 204, r.text
    DbRecipientDirectory().forget_push_token(
        PushTarget(device_id=device_id, platform="IOS", token="apns-test-token")
    )
    with user_tx(UUID(user.tokens["user_id"]), phone_hash(phone), None) as conn:
        assert (
            conn.execute(select(devices.c.apns_token).where(devices.c.id == device_id)).scalar_one()
            == "fresh-token"
        )
