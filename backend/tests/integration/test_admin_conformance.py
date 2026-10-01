"""Live admin API responses validated against contracts/openapi/admin.yaml, and table metadata checked
against the migrated schema."""

from __future__ import annotations

import json
from datetime import timedelta
from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest
import yaml
from fastapi.testclient import TestClient
from httpx import Response
from jsonschema_path import SchemaPath
from openapi_core import Config, OpenAPI
from openapi_core.contrib.requests import RequestsOpenAPIRequest, RequestsOpenAPIResponse
from requests import Request
from requests.models import Response as RequestsResponse
from sqlalchemy import text
from sqlalchemy.engine import Engine

from app.core.tables import metadata
from app.modules.client.directory import DbRecipientDirectory
from app.modules.delivery import service as delivery
from app.modules.delivery.adapters import MockPushSender
from app.scripts.seed import SeededTenant, seed_tenant

from .conftest import AppUser, Clock, auth, intent_body, portal_member, staff_member, token_for

pytestmark = pytest.mark.integration
SPEC = Path(__file__).resolve().parents[3] / "contracts/openapi/admin.yaml"
SERVER = "https://api.ringsays.example/admin/v1"
PDF = b"%PDF-1.4\n%mock\n%%EOF\n"


@pytest.fixture(scope="module")
def openapi() -> OpenAPI:
    spec = SchemaPath.from_dict(yaml.safe_load(SPEC.read_text()), base_uri=SPEC.as_uri())
    return OpenAPI(
        spec, config=Config(extra_media_type_deserializers={"application/problem+json": json.loads})
    )


class Checked:
    """Calls the API and validates every response against the contract."""

    def __init__(self, client: TestClient, openapi: OpenAPI, headers: dict[str, str]) -> None:
        self.client, self.openapi, self.headers = client, openapi, headers
        self.operations: set[str] = set()

    def __call__(self, method: str, path: str, expect: int, body: Any = None, **kw: Any) -> Any:
        resp: Response = getattr(self.client, method)(
            "/admin/v1" + path, headers=self.headers, **({"json": body} if body is not None else {}), **kw
        )
        assert resp.status_code == expect, f"{method} {path}: {resp.status_code} {resp.text}"
        if resp.status_code == 204:
            return None
        req = Request(method.upper(), SERVER + path, json=body, headers={"Content-Type": "application/json"})
        r = RequestsResponse()
        r.status_code = resp.status_code
        r._content = resp.content
        r.headers.update({k: v for k, v in resp.headers.items() if k.lower() == "content-type"})
        self.openapi.validate_response(RequestsOpenAPIRequest(req.prepare()), RequestsOpenAPIResponse(r))
        return resp.json()


def test_admin_responses_match_contract(
    client: TestClient, owner_engine: Engine, tenant: SeededTenant, clock: Clock, openapi: OpenAPI
) -> None:
    admin = portal_member(client, owner_engine, tenant, ["TENANT_ADMIN"])
    integ = portal_member(client, owner_engine, tenant, ["INTEGRATION_ADMIN"])
    t = f"/tenants/{tenant.tenant_id}"
    call = Checked(client, openapi, admin.headers)
    icall = Checked(client, openapi, integ.headers)

    call("get", "/me", 200)
    call("get", t, 200)
    dept = call("post", f"{t}/departments", 201, {"name": {"en": "Cards", "ar": "البطاقات"}})
    call(
        "patch", f"{t}/departments/{dept['department_id']}", 200, {"name": {"en": "Cards+", "ar": "البطاقات"}}
    )
    call("get", f"{t}/departments", 200)
    body = {
        "agent_id": "agt_conf",
        "display_name": {"en": "A", "ar": "أ"},
        "department_id": dept["department_id"],
    }
    call("post", f"{t}/agents", 201, body)
    call("patch", f"{t}/agents/agt_conf", 200, {"employee_ref": None})
    call("get", f"{t}/agents", 200)
    call("post", f"{t}/agents", 409, body)
    num = call(
        "post", f"{t}/calling-numbers", 201, {"phone": "+966115550000", "cst_entity_name_registered": True}
    )
    call("get", f"{t}/calling-numbers", 200)
    call("get", f"{t}/verification", 200)
    call("get", f"{t}/users", 200)
    call(
        "post", f"{t}/users", 201, {"email": f"c.{uuid4().hex[:6]}@mockbank.example", "roles": ["SUPERVISOR"]}
    )
    call("get", f"{t}/purpose-codes", 200)
    code = {
        "code": "CONF.TEST.CODE",
        "display_text": {"en": "Conformance", "ar": "اختبار"},
        "max_priority": "LOW",
        "max_duration_min": 5,
        "allowed_channels": ["PSTN"],
    }
    call("post", f"{t}/purpose-codes", 201, code)
    call("post", f"{t}/purpose-codes/CONF.TEST.CODE/retire", 200, {"reason": "test"})
    call("post", f"{t}/purpose-codes", 409, code)

    icall("get", f"{t}/api-clients", 200)
    created = icall("post", f"{t}/api-clients", 201, {"label": "conf", "scopes": ["intents:read"]})
    icall("post", f"{t}/api-clients/{created['client_id']}/revoke", 200, {"reason": "test"})
    ep = icall(
        "post",
        f"{t}/webhook-endpoints",
        201,
        {"url": "https://hooks.mockbank.example/c", "events": ["intent.delivered"]},
    )
    icall("get", f"{t}/webhook-endpoints", 200)

    user = AppUser(client, f"+9665{uuid4().int % 10**8:08d}")
    r = client.post(
        "/v1/intents",
        json=intent_body(tenant, to={"phone": user.phone}, channel_preference=["PRECALL_PUSH", "PSTN"]),
        headers=auth(token_for(client, tenant), str(uuid4())),
    )
    intent_id = r.json()["intent_id"]
    delivery.deliver_due(clock.now + timedelta(minutes=30), DbRecipientDirectory(), MockPushSender())
    deliveries = icall("get", f"{t}/webhook-endpoints/{ep['endpoint_id']}/deliveries", 200)
    icall("post", f"{t}/webhook-deliveries/{deliveries['items'][0]['delivery_id']}/replay", 409)
    icall("post", f"{t}/webhook-endpoints/{ep['endpoint_id']}/disable", 200, {"reason": "test"})

    call("get", f"{t}/intents", 200)
    call("get", f"{t}/intents?status=DELIVERED", 200)
    call("get", f"{t}/intents/summary", 200)
    call("get", f"{t}/intents/{intent_id}", 200)
    call("get", f"{t}/intents/{uuid4()}", 404)
    call("get", f"{t}/audit-events", 200)
    call("get", f"{t}/audit-events/verify", 200)
    call("post", f"{t}/calling-numbers/{num['number_id']}/revoke", 200, {"reason": "test"})
    call("get", f"/tenants/{uuid4()}", 403)
    call("patch", t, 409, {"domain": "x.example"})  # verified tenant: profile locked


def test_backoffice_responses_match_contract(
    client: TestClient, owner_engine: Engine, openapi: OpenAPI
) -> None:
    reviewer = staff_member(client, owner_engine, ["RS_REVIEWER"])
    ops = staff_member(client, owner_engine, ["RS_ADMIN"])
    rcall = Checked(client, openapi, reviewer.headers)
    ocall = Checked(client, openapi, ops.headers)
    created = ocall(
        "post",
        "/backoffice/tenants",
        201,
        {
            "legal_name": {"en": "Conf Bank", "ar": "بنك"},
            "sector": "INSURANCE",
            "admin_email": "a@conf.example",
        },
    )
    ocall("get", "/backoffice/tenants", 200)
    ocall("post", f"/backoffice/tenants/{created['tenant_id']}/suspend", 200, {"reason": "test"})
    ocall("post", f"/backoffice/tenants/{created['tenant_id']}/suspend", 409, {"reason": "test"})
    ocall("post", f"/backoffice/tenants/{created['tenant_id']}/reinstate", 200, {"reason": "test"})

    t = seed_tenant(owner_engine, verification_status="PENDING")
    admin = portal_member(client, owner_engine, t, ["TENANT_ADMIN"])
    acall = Checked(client, openapi, admin.headers)
    tp = f"/tenants/{t.tenant_id}"
    acall("patch", tp, 200, {"commercial_registration": "1010000001", "domain": "conf.example"})
    for kind in ("COMMERCIAL_REGISTRATION", "REGULATOR_LICENCE", "AUTHORISATION_LETTER"):
        acall("post", f"{tp}/verification/documents", 201, data={"kind": kind},
              files={"file": ("e.pdf", PDF, "application/pdf")})  # fmt: skip
    req = acall("post", f"{tp}/verification/submit", 201)
    num = acall(
        "post", f"{tp}/calling-numbers", 201, {"phone": "+966115551111", "cst_entity_name_registered": True}
    )
    acall(
        "post",
        f"{tp}/purpose-codes",
        201,
        {
            "code": "CONF.REVIEW.ME",
            "display_text": {"en": "Review", "ar": "مراجعة"},
            "max_priority": "LOW",
            "max_duration_min": 5,
            "allowed_channels": ["PSTN"],
        },
    )
    rcall("get", "/review/queue", 200)
    rcall("get", f"/review/verifications/{req['request_id']}", 200)
    rcall(
        "post", f"/review/verifications/{req['request_id']}", 200, {"decision": "APPROVE", "reason": "ok ok"}
    )
    rcall(
        "post", f"/review/verifications/{req['request_id']}", 409, {"decision": "APPROVE", "reason": "ok ok"}
    )
    rcall(
        "post",
        f"/review/purpose-codes/{t.tenant_id}/CONF.REVIEW.ME",
        200,
        {"decision": "REJECT", "reason": "vague"},
    )
    rcall(
        "post",
        f"/review/calling-numbers/{num['number_id']}",
        200,
        {"decision": "APPROVE", "reason": "CST ok"},
    )
    rcall("get", f"/review/verifications/{uuid4()}", 404)


def test_table_metadata_matches_database(owner_engine: Engine) -> None:
    """Every column the code queries exists in the migrated schema."""
    with owner_engine.connect() as c:
        rows = c.execute(
            text(
                "SELECT table_schema, table_name, column_name FROM information_schema.columns "
                "WHERE table_schema IN ('enterprise','intent','platform','audit','context','identity')"
            )
        ).all()
    actual = {(r.table_schema, r.table_name, r.column_name) for r in rows}
    missing = [
        (t.schema, t.name, col.name)
        for t in metadata.tables.values()
        for col in t.columns
        if (t.schema, t.name, col.name) not in actual
    ]
    assert missing == []


def test_admin_operations_all_exercised() -> None:
    """Guard against contract drift: every admin operation id has a route."""
    from app.main import app

    spec = yaml.safe_load(SPEC.read_text())
    served = app.openapi()["paths"]
    routes = {(m.upper(), p) for p, ops in served.items() for m in ops}
    missing = []
    for path, item in spec["paths"].items():
        for method in ("get", "post", "patch", "put", "delete"):
            if method in item and (method.upper(), "/admin/v1" + path) not in routes:
                missing.append(f"{method.upper()} {path}")
    assert missing == []
