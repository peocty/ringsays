"""Live responses validated against contracts/openapi/enterprise.yaml (including shared components)."""

from __future__ import annotations

import json
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
from requests import PreparedRequest, Request
from requests.models import Response as RequestsResponse
from sqlalchemy import text

from app.core.db import tenant_tx
from app.modules.audit import service as audit
from app.modules.intent import service
from app.modules.intent.domain import Channel
from app.scripts.seed import SeededTenant

from .conftest import Clock, auth, intent_body, token_for

pytestmark = pytest.mark.integration

SPEC_FILE = Path(__file__).resolve().parents[3] / "contracts/openapi/enterprise.yaml"
SERVER = "https://api.ringsays.example/v1"


@pytest.fixture(scope="module")
def openapi() -> OpenAPI:
    spec = SchemaPath.from_dict(yaml.safe_load(SPEC_FILE.read_text()), base_uri=SPEC_FILE.as_uri())
    return OpenAPI(
        spec, config=Config(extra_media_type_deserializers={"application/problem+json": json.loads})
    )


def _check(openapi: OpenAPI, method: str, path: str, resp: Response, body: Any = None) -> None:
    """Validate a TestClient response as if served from contract server URL."""
    req: PreparedRequest = Request(
        method, SERVER + path, json=body, headers={"Content-Type": "application/json"}
    ).prepare()
    r = RequestsResponse()
    r.status_code = resp.status_code
    r._content = resp.content
    r.headers.update({"Content-Type": resp.headers["content-type"]})
    openapi.validate_response(RequestsOpenAPIRequest(req), RequestsOpenAPIResponse(r))


def test_lifecycle_responses_match_contract(
    client: TestClient, tenant: SeededTenant, clock: Clock, openapi: OpenAPI
) -> None:
    tok = token_for(client, tenant)
    body = intent_body(tenant)
    r = client.post("/v1/intents", json=body, headers=auth(tok, str(uuid4())))
    _check(openapi, "POST", "/intents", r, body)
    intent_id = r.json()["intent_id"]

    _check(openapi, "GET", f"/intents/{intent_id}", client.get(f"/v1/intents/{intent_id}", headers=auth(tok)))

    with tenant_tx(tenant.tenant_id) as conn:
        service.mark_delivered(conn, intent_id, Channel.SDK, clock.now)
    r = client.post(f"/v1/intents/{intent_id}/cancel", headers=auth(tok, str(uuid4())))
    _check(openapi, "POST", f"/intents/{intent_id}/cancel", r)

    r = client.post(f"/v1/intents/{intent_id}/cancel", headers=auth(tok, str(uuid4())))
    assert r.status_code == 409
    _check(openapi, "POST", f"/intents/{intent_id}/cancel", r)

    r = client.get("/v1/purpose-codes?limit=3", headers=auth(tok))
    _check(openapi, "GET", "/purpose-codes", r)
    assert r.json()["next_cursor"] is not None


def test_error_responses_match_contract(client: TestClient, tenant: SeededTenant, openapi: OpenAPI) -> None:
    tok = token_for(client, tenant)
    body = intent_body(tenant, priority="URGENT")
    r = client.post("/v1/intents", json=body, headers=auth(tok, str(uuid4())))
    assert r.status_code == 422
    _check(openapi, "POST", "/intents", r, body)
    r = client.get(f"/v1/intents/{uuid4()}", headers=auth(tok))
    _check(openapi, "GET", f"/intents/{uuid4()}", r)


def test_forged_audit_row_breaks_chain(client: TestClient, tenant: SeededTenant, owner_engine) -> None:  # type: ignore[no-untyped-def]
    tok = token_for(client, tenant)
    client.post("/v1/intents", json=intent_body(tenant), headers=auth(tok, str(uuid4())))
    with owner_engine.begin() as c:
        c.execute(text("SELECT set_config('app.tenant_id', :t, true)"), {"t": str(tenant.tenant_id)})
        last = c.execute(
            text("SELECT hash FROM audit.audit_events WHERE tenant_id=:t ORDER BY id DESC LIMIT 1"),
            {"t": tenant.tenant_id},
        ).scalar_one()
        c.execute(
            text(
                "INSERT INTO audit.audit_events (event_id, tenant_id, at, actor, action, object_type, "
                "object_id, prev_hash, hash) VALUES (:e, :t, now(), 'attacker', 'intent.create', 'intent', "
                "'x', :p, :h)"
            ),
            {"e": uuid4(), "t": tenant.tenant_id, "p": last, "h": "f" * 64},
        )
    with tenant_tx(tenant.tenant_id) as conn:
        intact, _ = audit.verify_chain(conn, tenant.tenant_id)
    assert not intact
