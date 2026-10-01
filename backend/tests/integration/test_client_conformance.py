"""Live client API responses validated against contracts/openapi/client.yaml."""

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

from app.modules.client.directory import DbRecipientDirectory
from app.modules.delivery import service as delivery
from app.modules.delivery.adapters import MockPushSender
from app.scripts.seed import SeededTenant

from .conftest import AppUser, Clock, auth, intent_body, token_for

pytestmark = pytest.mark.integration
SPEC = Path(__file__).resolve().parents[3] / "contracts/openapi/client.yaml"
SERVER = "https://api.ringsays.example/v1"


@pytest.fixture(scope="module")
def openapi() -> OpenAPI:
    spec = SchemaPath.from_dict(yaml.safe_load(SPEC.read_text()), base_uri=SPEC.as_uri())
    return OpenAPI(
        spec, config=Config(extra_media_type_deserializers={"application/problem+json": json.loads})
    )


def _check(
    openapi: OpenAPI,
    method: str,
    path: str,
    resp: Response,
    body: Any = None,
    headers: dict[str, str] | None = None,
) -> None:
    req = Request(
        method, SERVER + path, json=body, headers={"Content-Type": "application/json", **(headers or {})}
    )
    r = RequestsResponse()
    r.status_code = resp.status_code
    r._content = resp.content
    r.headers.update({k: v for k, v in resp.headers.items() if k.lower() in ("content-type", "etag")})
    openapi.validate_response(RequestsOpenAPIRequest(req.prepare()), RequestsOpenAPIResponse(r))


def test_client_responses_match_contract(
    client: TestClient, tenant: SeededTenant, clock: Clock, openapi: OpenAPI
) -> None:
    phone = f"+9665{uuid4().int % 10**8:08d}"
    user = AppUser(client, phone)
    body = intent_body(tenant, to={"phone": phone}, channel_preference=["PRECALL_PUSH", "PSTN"])
    r = client.post("/v1/intents", json=body, headers=auth(token_for(client, tenant), str(uuid4())))
    intent_id = r.json()["intent_id"]
    delivery.deliver_due(clock.now + timedelta(minutes=30), DbRecipientDirectory(), MockPushSender())

    _check(openapi, "GET", "/inbox", client.get("/v1/inbox", headers=user.headers))
    _check(
        openapi,
        "GET",
        f"/me/intents/{intent_id}",
        client.get(f"/v1/me/intents/{intent_id}", headers=user.headers),
    )
    clock.now += timedelta(minutes=30)
    respond = {"action": "LATER", "later_minutes": 30}
    _check(
        openapi,
        "POST",
        f"/intents/{intent_id}/respond",
        client.post(f"/v1/intents/{intent_id}/respond", headers=user.headers, json=respond),
        respond,
    )
    prefs = client.get("/v1/me/preferences", headers=user.headers)
    _check(openapi, "GET", "/me/preferences", prefs)
    _check(openapi, "GET", "/me/consents", client.get("/v1/me/consents", headers=user.headers))
    _check(openapi, "POST", "/me/export", client.post("/v1/me/export", headers=user.headers))
    otp = {"phone": phone}
    _check(openapi, "POST", "/auth/otp", client.post("/v1/auth/otp", json=otp), otp)
