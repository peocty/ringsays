"""Signed webhooks: enqueue in transaction, ordered dispatch, retry, dead letter, replay, contract shape."""

from __future__ import annotations

import json
from datetime import timedelta
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import pytest
import yaml
from fastapi.testclient import TestClient
from jsonschema_path import SchemaPath
from openapi_schema_validator import OAS31Validator
from sqlalchemy import select

from app.core.db import tenant_tx, worker_tx
from app.core.tables import webhook_deliveries
from app.modules.delivery import service as delivery
from app.modules.delivery.adapters import MockPushSender, MockRecipientDirectory
from app.modules.intent.errors import RuleViolation
from app.modules.webhooks import service as webhooks
from app.modules.webhooks.service import MockHttpSender
from app.scripts.seed import SeededTenant

from .conftest import Clock, auth, intent_body, token_for

pytestmark = pytest.mark.integration

URL = "https://hooks.mockbank.example/ringsays"
SPEC = Path(__file__).resolve().parents[3] / "contracts/openapi/enterprise.yaml"


@pytest.fixture
def endpoint(tenant: SeededTenant) -> tuple[UUID, str]:
    with tenant_tx(tenant.tenant_id) as conn:
        return webhooks.create_endpoint(conn, tenant.tenant_id, URL, sorted(webhooks.ALL_EVENTS))


def _cancelled_intent(client: TestClient, t: SeededTenant) -> str:
    tok = token_for(client, t)
    r = client.post(
        "/v1/intents", json=intent_body(t, channel_preference=["PSTN"]), headers=auth(tok, str(uuid4()))
    )
    intent_id = str(r.json()["intent_id"])
    return intent_id


def _rows(t: SeededTenant, intent_id: str) -> list[Any]:
    with tenant_tx(t.tenant_id) as conn:
        return list(
            conn.execute(
                select(webhook_deliveries)
                .where(webhook_deliveries.c.intent_id == UUID(intent_id))
                .order_by(webhook_deliveries.c.id)
            ).all()
        )


def test_events_signed_ordered_and_match_contract(
    client: TestClient, tenant: SeededTenant, endpoint: tuple[UUID, str], clock: Clock
) -> None:
    _, secret = endpoint
    intent_id = _cancelled_intent(client, tenant)
    delivery.deliver_due(clock.now + timedelta(minutes=30), MockRecipientDirectory(), MockPushSender())
    tok = token_for(client, tenant)
    client.post(f"/v1/intents/{intent_id}/cancel", headers=auth(tok, str(uuid4())))
    sender = MockHttpSender()
    webhooks.dispatch_due(clock.now + timedelta(hours=1), sender)
    mine = [r for r in sender.requests if json.loads(r[1])["intent_id"] == intent_id]
    assert [json.loads(b)["type"] for _, b, _ in mine] == ["intent.delivered", "intent.cancelled"]
    spec = yaml.safe_load(SPEC.read_text())
    schema = SchemaPath.from_dict(spec, base_uri=SPEC.as_uri()) / "components" / "schemas" / "WebhookEvent"
    with schema.resolve() as resolved:
        validator = OAS31Validator(resolved.contents, _resolver=resolved.resolver)
        for url, body, headers in mine:
            assert url == URL
            payload = json.loads(body)
            validator.validate(payload)
            assert "+966" not in body.decode()
            ts = int(headers["RingSays-Signature"].split(",")[0][2:])
            assert webhooks.verify_signature(secret, headers["RingSays-Signature"], body, ts)
            assert not webhooks.verify_signature("whsec_wrong", headers["RingSays-Signature"], body, ts)
            assert not webhooks.verify_signature(secret, headers["RingSays-Signature"], body, ts + 301)
            assert headers["RingSays-Event-Id"] == payload["event_id"]
    assert all(r.status == "DELIVERED" for r in _rows(tenant, intent_id))


def test_failure_retries_with_backoff_and_blocks_later_events(
    client: TestClient, tenant: SeededTenant, endpoint: tuple[UUID, str], clock: Clock
) -> None:
    intent_id = _cancelled_intent(client, tenant)
    t = clock.now + timedelta(minutes=30)
    delivery.deliver_due(t, MockRecipientDirectory(), MockPushSender())
    client.post(f"/v1/intents/{intent_id}/cancel", headers=auth(token_for(client, tenant), str(uuid4())))
    with worker_tx() as conn:  # drain other tenants' rows from earlier tests
        conn.execute(
            webhook_deliveries.update()
            .where(webhook_deliveries.c.intent_id != UUID(intent_id))
            .values(status="DELIVERED")
        )
    sender = MockHttpSender(responses={URL: [500]})
    webhooks.dispatch_due(t, sender)
    first, second = _rows(tenant, intent_id)
    assert (first.status, first.attempts, first.last_status_code) == ("PENDING", 1, 500)
    assert second.attempts == 0, "later event waits for earlier one"
    assert first.next_attempt_at == t + timedelta(seconds=webhooks.FIRST_BACKOFF_S)
    webhooks.dispatch_due(t + timedelta(seconds=10), sender)
    assert _rows(tenant, intent_id)[0].attempts == 1, "not retried before backoff"
    webhooks.dispatch_due(t + timedelta(seconds=31), sender)
    webhooks.dispatch_due(t + timedelta(seconds=32), sender)
    assert [r.status for r in _rows(tenant, intent_id)] == ["DELIVERED", "DELIVERED"]


def test_dead_after_retry_window_then_replay(
    client: TestClient, tenant: SeededTenant, endpoint: tuple[UUID, str], clock: Clock
) -> None:
    intent_id = _cancelled_intent(client, tenant)
    t = clock.now + timedelta(minutes=30)
    delivery.deliver_due(t, MockRecipientDirectory(), MockPushSender())
    always_down = MockHttpSender(responses={URL: [503] * 100})
    for hours in range(0, 30):
        webhooks.dispatch_due(t + timedelta(hours=hours), always_down)
    row = _rows(tenant, intent_id)[0]
    assert row.status == "DEAD" and row.attempts > 5
    with tenant_tx(tenant.tenant_id) as conn:
        webhooks.replay(conn, row.id, t + timedelta(days=2))
    ok = MockHttpSender()
    webhooks.dispatch_due(t + timedelta(days=2), ok)
    replayed = [json.loads(b) for _, b, _ in ok.requests if json.loads(b)["intent_id"] == intent_id]
    assert replayed and replayed[0]["event_id"] == str(row.event_id), "same event id for receiver dedupe"


def test_replay_of_other_tenant_delivery_not_found(
    client: TestClient,
    tenant: SeededTenant,
    other_tenant: SeededTenant,
    endpoint: tuple[UUID, str],
    clock: Clock,
) -> None:
    intent_id = _cancelled_intent(client, tenant)
    delivery.deliver_due(clock.now + timedelta(minutes=30), MockRecipientDirectory(), MockPushSender())
    row = _rows(tenant, intent_id)[0]
    with pytest.raises(webhooks.DeliveryNotFound), tenant_tx(other_tenant.tenant_id) as conn:
        webhooks.replay(conn, row.id, clock.now)


@pytest.mark.parametrize(
    "url",
    [
        "http://hooks.example.com/x",
        "https://127.0.0.1/x",
        "https://10.0.0.5/x",
        "https://[::1]/x",
        "https://169.254.169.254/latest",
        "https://user:pw@hooks.example.com/x",
    ],
)
def test_endpoint_url_rejected(tenant: SeededTenant, url: str) -> None:
    with pytest.raises(RuleViolation), tenant_tx(tenant.tenant_id) as conn:
        webhooks.create_endpoint(conn, tenant.tenant_id, url, ["intent.accepted"])


def test_unsubscribed_events_not_queued(client: TestClient, tenant: SeededTenant, clock: Clock) -> None:
    with tenant_tx(tenant.tenant_id) as conn:
        webhooks.create_endpoint(conn, tenant.tenant_id, URL, ["outcome.recorded"])
    intent_id = _cancelled_intent(client, tenant)
    delivery.deliver_due(clock.now + timedelta(minutes=30), MockRecipientDirectory(), MockPushSender())
    assert _rows(tenant, intent_id) == []


def test_secret_stored_encrypted(tenant: SeededTenant, endpoint: tuple[UUID, str]) -> None:
    endpoint_id, secret = endpoint
    with worker_tx() as conn:
        stored = conn.execute(
            select(webhooks.webhook_endpoints.c.secret_ciphertext).where(
                webhooks.webhook_endpoints.c.id == endpoint_id
            )
        ).scalar_one()
    assert secret not in stored and secret.startswith("whsec_")
