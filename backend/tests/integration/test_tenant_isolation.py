"""Cross tenant leak suite. Every enterprise endpoint and every tenant table is probed from another tenant."""

from __future__ import annotations

from datetime import UTC
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Table, func, insert, select, text
from sqlalchemy.exc import DBAPIError, ProgrammingError

from app.core.db import anonymous_tx, tenant_tx
from app.core.tables import (
    agents,
    audit_events,
    calling_numbers,
    context_tokens,
    delivery_attempts,
    departments,
    idempotency_keys,
    intent_events,
    intents,
    outbox,
    purpose_codes,
    tenants,
    webhook_deliveries,
    webhook_endpoints,
)
from app.scripts.seed import SeededTenant

from .conftest import auth, intent_body, token_for

pytestmark = pytest.mark.integration

TENANT_TABLES: list[Table] = [
    departments,
    agents,
    calling_numbers,
    purpose_codes,
    intents,
    intent_events,
    idempotency_keys,
    audit_events,
    delivery_attempts,
    webhook_endpoints,
    webhook_deliveries,
    context_tokens,
]


@pytest.fixture
def a_intent(client: TestClient, tenant: SeededTenant) -> UUID:
    """Tenant A intent with token, webhook endpoint, delivery attempt and queued webhook."""
    from datetime import datetime

    from app.modules.delivery import service as delivery
    from app.modules.delivery.adapters import MockPushSender, MockRecipientDirectory
    from app.modules.webhooks import service as webhooks

    with tenant_tx(tenant.tenant_id) as conn:
        webhooks.create_endpoint(conn, tenant.tenant_id, "https://hooks.example.com/a", ["intent.delivered"])
    tok = token_for(client, tenant)
    r = client.post(
        "/v1/intents",
        json=intent_body(tenant, channel_preference=["SDK", "PSTN"]),
        headers=auth(tok, str(uuid4())),
    )
    assert r.status_code == 201
    opens = datetime(2026, 10, 4, 7, 30, tzinfo=UTC)
    delivery.deliver_due(opens, MockRecipientDirectory(), MockPushSender())
    delivery.deliver_due(opens + delivery.SDK_GRACE, MockRecipientDirectory(), MockPushSender())
    return UUID(r.json()["intent_id"])


@pytest.mark.parametrize(
    "method,path,body",
    [
        ("GET", "/v1/intents/{id}", None),
        ("POST", "/v1/intents/{id}/cancel", None),
        ("POST", "/v1/intents/{id}/calling", None),
        ("POST", "/v1/intents/{id}/outcome", {"code": "RESOLVED"}),
    ],
)
def test_other_tenant_gets_404_on_every_intent_endpoint(
    client: TestClient,
    a_intent: UUID,
    other_tenant: SeededTenant,
    method: str,
    path: str,
    body: dict[str, str] | None,
) -> None:
    tok_b = token_for(client, other_tenant)
    r = client.request(method, path.format(id=a_intent), json=body, headers=auth(tok_b, str(uuid4())))
    assert r.status_code == 404, r.text


def test_purpose_code_lists_are_separate(
    client: TestClient, tenant: SeededTenant, other_tenant: SeededTenant
) -> None:
    tok_a = token_for(client, tenant)
    tok_b = token_for(client, other_tenant)
    r = client.post(
        "/v1/purpose-codes",
        headers=auth(tok_a, str(uuid4())),
        json={
            "code": "TENANTA.ONLY.CODE",
            "display_text": {"en": "A only", "ar": "أ فقط"},
            "max_priority": "NORMAL",
            "max_duration_min": 5,
            "allowed_channels": ["SDK"],
        },
    )
    assert r.status_code == 201 and r.json()["status"] == "PENDING_REVIEW"
    codes_b = [
        c["code"] for c in client.get("/v1/purpose-codes?limit=200", headers=auth(tok_b)).json()["items"]
    ]
    assert "TENANTA.ONLY.CODE" not in codes_b


def test_other_tenant_department_rejected(
    client: TestClient, tenant: SeededTenant, other_tenant: SeededTenant
) -> None:
    tok_b = token_for(client, other_tenant)
    r = client.post(
        "/v1/intents",
        json=intent_body(other_tenant, department_id=str(tenant.department_id)),
        headers=auth(tok_b, str(uuid4())),
    )
    assert r.status_code == 422 and r.json()["code"] == "rule_violation"


def test_other_tenant_parent_intent_rejected(
    client: TestClient, a_intent: UUID, other_tenant: SeededTenant
) -> None:
    tok_b = token_for(client, other_tenant)
    for parent in (a_intent, uuid4()):
        r = client.post(
            "/v1/intents",
            json=intent_body(other_tenant, parent_intent_id=str(parent)),
            headers=auth(tok_b, str(uuid4())),
        )
        assert r.status_code == 422, r.text
        assert r.json()["code"] == "rule_violation"


@pytest.mark.parametrize("table", TENANT_TABLES, ids=lambda t: t.fullname)
def test_rows_invisible_across_tenants_and_without_tenant(
    a_intent: UUID, tenant: SeededTenant, other_tenant: SeededTenant, table: Table
) -> None:
    with tenant_tx(tenant.tenant_id) as conn:
        own = conn.execute(
            select(func.count()).select_from(table).where(table.c.tenant_id == tenant.tenant_id)
        ).scalar_one()
    with tenant_tx(other_tenant.tenant_id) as conn:
        leaked = conn.execute(
            select(func.count()).select_from(table).where(table.c.tenant_id == tenant.tenant_id)
        ).scalar_one()
    with anonymous_tx() as conn:
        anonymous = conn.execute(select(func.count()).select_from(table)).scalar_one()
    if table is not idempotency_keys:
        assert own > 0, f"seed produced no rows in {table.fullname}; test would prove nothing"
    assert leaked == 0
    assert anonymous == 0


def test_tenants_table_shows_only_own_row(tenant: SeededTenant, other_tenant: SeededTenant) -> None:
    with tenant_tx(other_tenant.tenant_id) as conn:
        ids = conn.execute(select(tenants.c.id)).scalars().all()
    assert ids == [other_tenant.tenant_id]


def test_outbox_invisible_to_app_role(a_intent: UUID, tenant: SeededTenant) -> None:
    with pytest.raises(ProgrammingError, match="permission denied"):
        with tenant_tx(tenant.tenant_id) as conn:
            conn.execute(select(outbox))


def test_cannot_write_rows_for_another_tenant(
    a_intent: UUID, tenant: SeededTenant, other_tenant: SeededTenant
) -> None:
    with pytest.raises(DBAPIError, match="row-level security"):
        with tenant_tx(other_tenant.tenant_id) as conn:
            conn.execute(
                insert(intent_events).values(
                    intent_id=a_intent,
                    tenant_id=tenant.tenant_id,
                    at=func.now(),
                    from_status="REQUESTED",
                    to_status="CANCELLED",
                    actor="CALLER",
                )
            )


def test_app_role_cannot_read_api_clients(tenant: SeededTenant) -> None:
    with pytest.raises(ProgrammingError, match="permission denied"):
        with tenant_tx(tenant.tenant_id) as conn:
            conn.execute(text("SELECT secret_hash FROM enterprise.api_clients"))


def test_idempotency_keys_are_per_tenant(
    client: TestClient, tenant: SeededTenant, other_tenant: SeededTenant
) -> None:
    key = str(uuid4())
    ra = client.post("/v1/intents", json=intent_body(tenant), headers=auth(token_for(client, tenant), key))
    rb = client.post(
        "/v1/intents", json=intent_body(other_tenant), headers=auth(token_for(client, other_tenant), key)
    )
    assert ra.status_code == rb.status_code == 201
    assert ra.json()["intent_id"] != rb.json()["intent_id"]
