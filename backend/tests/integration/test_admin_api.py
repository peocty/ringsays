"""Admin API: sign in, roles, organisation, verification, catalogue, integration, monitor, audit."""

from __future__ import annotations

import time
from datetime import timedelta
from typing import Any
from uuid import uuid4

import jwt
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.engine import Engine

from app.core.config import settings
from app.modules.client.directory import DbRecipientDirectory
from app.modules.delivery import service as delivery
from app.modules.delivery.adapters import MockPushSender
from app.modules.devoidc import issuer
from app.modules.webhooks import service as webhooks
from app.scripts.seed import SeededTenant, seed_tenant

from .conftest import (
    AppUser,
    Clock,
    PortalPerson,
    auth,
    intent_body,
    portal_member,
    staff_member,
    token_for,
    unique_email,
)

pytestmark = pytest.mark.integration
A = "/admin/v1"
PDF = b"%PDF-1.4\n%mock evidence\n1 0 obj<<>>endobj\ntrailer<<>>\n%%EOF\n"
PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32


def _t(t: SeededTenant) -> str:
    return f"{A}/tenants/{t.tenant_id}"


def _phone() -> str:
    return f"+9665{uuid4().int % 10**8:08d}"


def _send_intent(client: TestClient, t: SeededTenant, phone: str | None = None, **kw: Any) -> str:
    body = intent_body(t, to={"phone": phone or _phone()}, channel_preference=["PRECALL_PUSH", "PSTN"], **kw)
    r = client.post("/v1/intents", json=body, headers=auth(token_for(client, t), str(uuid4())))
    assert r.status_code == 201, r.text
    return str(r.json()["intent_id"])


@pytest.fixture
def admin(client: TestClient, owner_engine: Engine, tenant: SeededTenant) -> PortalPerson:
    return portal_member(client, owner_engine, tenant, ["TENANT_ADMIN"])


# Sign in and access


def test_me_binds_invitation_and_lists_permissions(
    client: TestClient, owner_engine: Engine, tenant: SeededTenant
) -> None:
    p = portal_member(client, owner_engine, tenant, ["SUPERVISOR"])
    [m] = p.me["memberships"]
    assert m["tenant_id"] == str(tenant.tenant_id)
    assert m["roles"] == ["SUPERVISOR"]
    assert set(m["permissions"]) == {"org.read", "intents.read_all"}
    assert p.me["staff_roles"] == []


def test_unknown_person_has_no_memberships_and_no_access(client: TestClient, tenant: SeededTenant) -> None:
    p = PortalPerson(client, unique_email("stranger"))
    assert p.me["memberships"] == []
    assert client.get(_t(tenant), headers=p.headers).status_code == 403


def test_member_of_one_tenant_cannot_reach_another(
    client: TestClient, owner_engine: Engine, tenant: SeededTenant, other_tenant: SeededTenant
) -> None:
    p = portal_member(client, owner_engine, tenant, ["TENANT_ADMIN"])
    assert client.get(_t(other_tenant), headers=p.headers).status_code == 403
    assert client.get(f"{_t(other_tenant)}/agents", headers=p.headers).status_code == 403
    # Non existent tenant looks the same as a foreign one.
    assert client.get(f"{A}/tenants/{uuid4()}", headers=p.headers).status_code == 403


def test_expired_invitation_does_not_bind(
    client: TestClient, owner_engine: Engine, tenant: SeededTenant
) -> None:
    email = unique_email("late")
    with owner_engine.begin() as c:
        c.execute(
            text(
                "INSERT INTO enterprise.portal_users (id, tenant_id, email, roles, status, invited_by, "
                "invite_expires_at) VALUES (:i, :t, :e, ARRAY['SUPERVISOR'], 'INVITED', 'test', now() - "
                "interval '1 day')"
            ),
            {"i": uuid4(), "t": tenant.tenant_id, "e": email},
        )
    assert PortalPerson(client, email).me["memberships"] == []


def test_unverified_email_does_not_bind(
    client: TestClient, owner_engine: Engine, tenant: SeededTenant
) -> None:
    from app.scripts.seed import invite_portal_user

    email = unique_email("unverified")
    invite_portal_user(owner_engine, tenant.tenant_id, email, ["SUPERVISOR"])
    claims = jwt.decode(issuer.tokens_for(email)["access_token"], options={"verify_signature": False})
    claims["email_verified"] = False
    token = jwt.encode(claims, issuer._private_key(), algorithm="RS256", headers={"kid": issuer.KID})
    r = client.get(f"{A}/me", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200 and r.json()["memberships"] == []


@pytest.mark.parametrize("problem", ["audience", "issuer", "expired", "signature", "none"])
def test_bad_tokens_rejected(client: TestClient, problem: str) -> None:
    from cryptography.hazmat.primitives.asymmetric import rsa

    claims = jwt.decode(
        issuer.tokens_for("x@mockbank.example")["access_token"], options={"verify_signature": False}
    )
    key: Any = issuer._private_key()
    alg = "RS256"
    if problem == "audience":
        claims["aud"] = "ringsays-enterprise-api"
    elif problem == "issuer":
        claims["iss"] = "https://evil.example"
    elif problem == "expired":
        claims["exp"] = int(time.time()) - 3600
    elif problem == "signature":
        key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    token = (
        jwt.encode(claims, key, algorithm=alg)
        if problem != "none"
        else jwt.encode(claims, None, algorithm="none")  # type: ignore[arg-type]
    )
    r = client.get(f"{A}/me", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 401


def test_enterprise_api_token_not_accepted_by_admin_api(client: TestClient, tenant: SeededTenant) -> None:
    r = client.get(_t(tenant), headers=auth(token_for(client, tenant)))
    assert r.status_code == 401


def test_disabled_person_loses_access_on_next_request(
    client: TestClient, owner_engine: Engine, tenant: SeededTenant, admin: PortalPerson
) -> None:
    sup = portal_member(client, owner_engine, tenant, ["SUPERVISOR"])
    assert client.get(_t(tenant), headers=sup.headers).status_code == 200
    users = client.get(f"{_t(tenant)}/users", headers=admin.headers).json()["items"]
    sup_id = next(u["user_id"] for u in users if u["email"] == sup.email)
    r = client.patch(f"{_t(tenant)}/users/{sup_id}", json={"status": "DISABLED"}, headers=admin.headers)
    assert r.status_code == 200, r.text
    assert client.get(_t(tenant), headers=sup.headers).status_code == 403


@pytest.mark.parametrize(
    "roles,method,path,body,expected",
    [
        (["SUPERVISOR"], "post", "/agents", {}, 403),
        (["AGENT"], "get", "/audit-events", None, 403),
        (["COMPLIANCE"], "post", "/api-clients", {"label": "x", "scopes": ["intents:read"]}, 403),
        (["TENANT_ADMIN"], "post", "/api-clients", {"label": "x", "scopes": ["intents:read"]}, 403),
        (["INTEGRATION_ADMIN"], "get", "/users", None, 403),
        (["INTEGRATION_ADMIN"], "get", "/intents", None, 403),
        (["SUPERVISOR"], "post", "/purpose-codes", {}, 403),
    ],
)
def test_role_matrix(
    client: TestClient,
    owner_engine: Engine,
    tenant: SeededTenant,
    roles: list[str],
    method: str,
    path: str,
    body: Any,
    expected: int,
) -> None:
    p = portal_member(
        client, owner_engine, tenant, roles, agent_id="agt_demo_01" if "AGENT" in roles else None
    )
    r = getattr(client, method)(
        _t(tenant) + path, headers=p.headers, **({"json": body} if body is not None else {})
    )
    assert r.status_code == expected, r.text


# Organisation


def test_departments_and_agents(client: TestClient, tenant: SeededTenant, other_tenant: SeededTenant,
                                admin: PortalPerson) -> None:  # fmt: skip
    h = admin.headers
    r = client.post(f"{_t(tenant)}/departments", json={"name": {"en": "Cards", "ar": "البطاقات"}}, headers=h)
    assert r.status_code == 201
    dept = r.json()["department_id"]
    body = {"agent_id": "agt_cards_01", "display_name": {"en": "Sara", "ar": "سارة"}, "department_id": dept}
    assert client.post(f"{_t(tenant)}/agents", json=body, headers=h).status_code == 201
    assert client.post(f"{_t(tenant)}/agents", json=body, headers=h).status_code == 409
    foreign = {**body, "agent_id": "agt_x_01", "department_id": str(other_tenant.department_id)}
    assert client.post(f"{_t(tenant)}/agents", json=foreign, headers=h).status_code == 422
    r = client.patch(f"{_t(tenant)}/agents/agt_cards_01", json={"active": False}, headers=h)
    assert r.status_code == 200 and r.json()["active"] is False
    page = client.get(f"{_t(tenant)}/agents?limit=1", headers=h).json()
    assert len(page["items"]) == 1 and page["next_cursor"]
    nxt = client.get(f"{_t(tenant)}/agents?limit=1&cursor={page['next_cursor']}", headers=h).json()
    assert nxt["items"][0]["agent_id"] != page["items"][0]["agent_id"]


def test_deactivated_agent_cannot_send(client: TestClient, tenant: SeededTenant, admin: PortalPerson) -> None:
    client.patch(f"{_t(tenant)}/agents/{tenant.agent_id}", json={"active": False}, headers=admin.headers)
    body = intent_body(tenant)
    r = client.post("/v1/intents", json=body, headers=auth(token_for(client, tenant), str(uuid4())))
    assert r.status_code == 422 and r.json()["code"] == "unknown_agent"


def test_calling_numbers_masked_pending_and_revocable(
    client: TestClient, tenant: SeededTenant, admin: PortalPerson
) -> None:
    h = admin.headers
    r = client.post(
        f"{_t(tenant)}/calling-numbers",
        json={"phone": "+966112345678", "cst_entity_name_registered": True},
        headers=h,
    )
    assert r.status_code == 201, r.text
    n = r.json()
    assert n["status"] == "PENDING_VERIFICATION" and n["phone_masked"] == "+9661•••••678"
    assert "+966112345678" not in client.get(f"{_t(tenant)}/calling-numbers", headers=h).text
    dup = client.post(
        f"{_t(tenant)}/calling-numbers",
        json={"phone": "+966112345678", "cst_entity_name_registered": True},
        headers=h,
    )
    assert dup.status_code == 409
    r = client.post(
        f"{_t(tenant)}/calling-numbers/{n['number_id']}/revoke", json={"reason": "moved"}, headers=h
    )
    assert r.json()["status"] == "REVOKED"


def test_database_refuses_self_approval_by_api_role(database: dict[str, str], tenant: SeededTenant) -> None:
    """Defence in depth: even raw SQL as the API role cannot verify a number or approve a code."""
    from sqlalchemy import create_engine
    from sqlalchemy.exc import DBAPIError

    eng = create_engine(database["app"])
    stmts = [
        "INSERT INTO enterprise.calling_numbers (id, tenant_id, phone, status) VALUES (gen_random_uuid(), "
        "platform.current_tenant(), '+966118888888', 'PENDING_VERIFICATION'); "
        "UPDATE enterprise.calling_numbers SET status = 'VERIFIED' WHERE phone = '+966118888888'",
        "UPDATE enterprise.calling_numbers SET status = 'PENDING_VERIFICATION'",
        "UPDATE enterprise.calling_numbers SET reviewed_by = 'me'",
        "INSERT INTO enterprise.calling_numbers (id, tenant_id, phone, status) VALUES (gen_random_uuid(), "
        "platform.current_tenant(), '+966117777777', 'VERIFIED')",
        "UPDATE enterprise.purpose_codes SET status = 'PENDING_REVIEW'",
        "UPDATE enterprise.purpose_codes SET max_priority = 'URGENT'",
        "UPDATE enterprise.purpose_codes SET reviewed_by = 'me'",
        "UPDATE enterprise.tenants SET verification_status = 'VERIFIED'",
        "INSERT INTO enterprise.purpose_codes (tenant_id, code, display_en, display_ar, max_priority, "
        "max_duration_min, allowed_channels, status) VALUES (platform.current_tenant(), 'X.Y', 'x', 'x', "
        "'LOW', 5, ARRAY['PSTN'], 'APPROVED')",
        "INSERT INTO enterprise.verification_requests (id, tenant_id, status, document_ids, submitted_by, "
        "submitted_at, decided_at) VALUES (gen_random_uuid(), platform.current_tenant(), 'APPROVED', '{}', "
        "'x', now(), now())",
        "SELECT * FROM enterprise.api_clients",
        "SELECT * FROM platform.staff_users",
    ]
    for stmt in stmts:
        with pytest.raises(DBAPIError):
            with eng.begin() as c:
                c.execute(text("SELECT set_config('app.tenant_id', :t, true)"), {"t": str(tenant.tenant_id)})
                c.execute(text(stmt))
    eng.dispose()


def test_backoffice_role_cannot_read_intents_or_identity(database: dict[str, str]) -> None:
    from sqlalchemy import create_engine
    from sqlalchemy.exc import DBAPIError

    eng = create_engine(database["backoffice"])
    for stmt in [
        "SELECT 1 FROM intent.intents",
        "SELECT 1 FROM identity.users",
        "SELECT 1 FROM context.context_tokens",
    ]:
        with pytest.raises(DBAPIError):
            with eng.begin() as c:
                c.execute(text(stmt))
    eng.dispose()


# Verification


def _pending_tenant(client: TestClient, owner_engine: Engine) -> tuple[SeededTenant, PortalPerson]:
    t = seed_tenant(owner_engine, verification_status="PENDING")
    return t, portal_member(client, owner_engine, t, ["TENANT_ADMIN"])


def _upload(client: TestClient, t: SeededTenant, p: PortalPerson, kind: str, data: bytes = PDF,
            name: str = "evidence.pdf") -> Any:  # fmt: skip
    return client.post(
        f"{_t(t)}/verification/documents",
        data={"kind": kind, "reference": "1010101010"},
        files={"file": (name, data, "application/pdf")},
        headers=p.headers,
    )


def test_verification_end_to_end(client: TestClient, owner_engine: Engine) -> None:
    t, p = _pending_tenant(client, owner_engine)
    reviewer = staff_member(client, owner_engine, ["RS_REVIEWER"])
    v = client.get(f"{_t(t)}/verification", headers=p.headers).json()
    assert v["required_kinds"] == ["COMMERCIAL_REGISTRATION", "REGULATOR_LICENCE", "AUTHORISATION_LETTER"]
    # Intents refused while pending.
    r = client.post("/v1/intents", json=intent_body(t), headers=auth(token_for(client, t), str(uuid4())))
    assert r.status_code == 403
    assert client.post(f"{_t(t)}/verification/submit", headers=p.headers).status_code == 422
    r = client.patch(
        _t(t), json={"commercial_registration": "1010123456", "domain": "MockBank.example"}, headers=p.headers
    )
    assert r.status_code == 200 and r.json()["domain"] == "mockbank.example"
    for kind in v["required_kinds"]:
        assert _upload(client, t, p, kind).status_code == 201
    r = client.post(f"{_t(t)}/verification/submit", headers=p.headers)
    assert r.status_code == 201, r.text
    request_id = r.json()["request_id"]
    # Locked while under review.
    assert client.patch(_t(t), json={"domain": "other.example"}, headers=p.headers).status_code == 409
    assert _upload(client, t, p, "OTHER").status_code == 409
    assert client.post(f"{_t(t)}/verification/submit", headers=p.headers).status_code == 409
    # Reviewer sees it in queue, downloads evidence, approves.
    q = client.get(f"{A}/review/queue", headers=reviewer.headers).json()
    assert request_id in [x["request_id"] for x in q["verifications"]]
    detail = client.get(f"{A}/review/verifications/{request_id}", headers=reviewer.headers).json()
    assert len(detail["documents"]) == 3
    f = client.get(
        f"{A}/review/documents/{detail['documents'][0]['document_id']}/file", headers=reviewer.headers
    )
    assert f.status_code == 200 and f.content == PDF and f.headers["x-content-type-options"] == "nosniff"
    r = client.post(
        f"{A}/review/verifications/{request_id}",
        json={"decision": "APPROVE", "reason": "CR and SAMA licence checked"},
        headers=reviewer.headers,
    )
    assert r.status_code == 200 and r.json()["status"] == "APPROVED"
    assert client.get(_t(t), headers=p.headers).json()["verification_status"] == "VERIFIED"
    again = client.post(
        f"{A}/review/verifications/{request_id}",
        json={"decision": "REJECT", "reason": "changed mind"},
        headers=reviewer.headers,
    )
    assert again.status_code == 409
    r = client.post("/v1/intents", json=intent_body(t), headers=auth(token_for(client, t), str(uuid4())))
    assert r.status_code == 201
    # Decision is in the tenant's own audit chain, and chain is intact.
    events = client.get(f"{_t(t)}/audit-events", headers=p.headers).json()["items"]
    approved = next(e for e in events if e["action"] == "verification.approved")
    assert approved["actor"].startswith("staff:") and approved["reason"] == "CR and SAMA licence checked"
    assert client.get(f"{_t(t)}/audit-events/verify", headers=p.headers).json()["intact"] is True


def test_rejected_verification_can_be_resubmitted(client: TestClient, owner_engine: Engine) -> None:
    t, p = _pending_tenant(client, owner_engine)
    reviewer = staff_member(client, owner_engine, ["RS_REVIEWER"])
    client.patch(
        _t(t), json={"commercial_registration": "1010123456", "domain": "bank.example"}, headers=p.headers
    )
    for kind in ("COMMERCIAL_REGISTRATION", "REGULATOR_LICENCE", "AUTHORISATION_LETTER"):
        _upload(client, t, p, kind)
    rid = client.post(f"{_t(t)}/verification/submit", headers=p.headers).json()["request_id"]
    client.post(
        f"{A}/review/verifications/{rid}",
        json={"decision": "REJECT", "reason": "Licence expired"},
        headers=reviewer.headers,
    )
    v = client.get(f"{_t(t)}/verification", headers=p.headers).json()
    assert v["tenant_status"] == "PENDING" and v["latest_request"]["decision_reason"] == "Licence expired"
    doc = next(d for d in v["documents"] if d["kind"] == "REGULATOR_LICENCE")
    assert (
        client.delete(f"{_t(t)}/verification/documents/{doc['document_id']}", headers=p.headers).status_code
        == 204
    )
    _upload(client, t, p, "REGULATOR_LICENCE")
    assert client.post(f"{_t(t)}/verification/submit", headers=p.headers).status_code == 201


def test_upload_rejects_wrong_type_and_oversize(client: TestClient, owner_engine: Engine) -> None:
    t, p = _pending_tenant(client, owner_engine)
    r = _upload(client, t, p, "OTHER", data=b"<html><script>alert(1)</script>", name="x.pdf")
    assert r.status_code == 422
    r = _upload(client, t, p, "OTHER", data=b"%PDF-" + b"0" * (5 * 1024 * 1024), name="big.pdf")
    assert r.status_code == 422
    r = _upload(client, t, p, "OTHER", data=PNG, name="../../etc/passwd")
    assert r.status_code == 201
    assert r.json()["content_type"] == "image/png" and r.json()["file_name"] == "passwd"


def test_tenant_cannot_use_review_routes(
    client: TestClient, owner_engine: Engine, admin: PortalPerson
) -> None:
    assert client.get(f"{A}/review/queue", headers=admin.headers).status_code == 403
    reviewer = staff_member(client, owner_engine, ["RS_REVIEWER"])
    assert (
        client.post(
            f"{A}/backoffice/tenants",
            json={"legal_name": {"en": "X", "ar": "س"}, "sector": "BANK", "admin_email": "a@x.example"},
            headers=reviewer.headers,
        ).status_code
        == 403
    )


def test_backoffice_disabled_answers_404(client: TestClient, owner_engine: Engine) -> None:
    reviewer = staff_member(client, owner_engine, ["RS_REVIEWER"])
    settings.backoffice_enabled = False
    try:
        assert client.get(f"{A}/review/queue", headers=reviewer.headers).status_code == 404
    finally:
        settings.backoffice_enabled = True


# Onboarding and suspension


def test_onboard_tenant_invites_first_admin(client: TestClient, owner_engine: Engine) -> None:
    ops = staff_member(client, owner_engine, ["RS_ADMIN"])
    email = unique_email("newbank")
    r = client.post(
        f"{A}/backoffice/tenants",
        json={"legal_name": {"en": "New Bank", "ar": "بنك جديد"}, "sector": "BANK", "admin_email": email},
        headers=ops.headers,
    )
    assert r.status_code == 201, r.text
    tid = r.json()["tenant_id"]
    assert r.json()["verification_status"] == "PENDING" and r.json()["residency_region"] == "KSA"
    person = PortalPerson(client, email)
    assert [m["tenant_id"] for m in person.me["memberships"]] == [tid]
    assert person.me["memberships"][0]["roles"] == ["TENANT_ADMIN"]
    listed = client.get(f"{A}/backoffice/tenants?status=PENDING", headers=ops.headers).json()["items"]
    assert tid in [x["tenant_id"] for x in listed]


def test_suspend_blocks_api_and_portal_writes(
    client: TestClient, owner_engine: Engine, tenant: SeededTenant, admin: PortalPerson
) -> None:
    ops = staff_member(client, owner_engine, ["RS_ADMIN"])
    token = token_for(client, tenant)
    r = client.post(f"{A}/backoffice/tenants/{tenant.tenant_id}/suspend", json={"reason": "Fraud report"},
                    headers=ops.headers)  # fmt: skip
    assert r.status_code == 200 and r.json()["verification_status"] == "SUSPENDED"
    assert (
        client.post("/v1/intents", json=intent_body(tenant), headers=auth(token, str(uuid4()))).status_code
        == 401
    )
    # Portal still readable (shows reason) but writes refused.
    assert client.get(_t(tenant), headers=admin.headers).json()["suspended_reason"] == "Fraud report"
    r = client.post(f"{_t(tenant)}/departments", json={"name": {"en": "X", "ar": "س"}}, headers=admin.headers)
    assert r.status_code == 403
    r = client.post(f"{A}/backoffice/tenants/{tenant.tenant_id}/reinstate", json={"reason": "Cleared"},
                    headers=ops.headers)  # fmt: skip
    assert r.json()["verification_status"] == "VERIFIED"


# Catalogue


def test_purpose_code_review_cycle(client: TestClient, owner_engine: Engine, tenant: SeededTenant,
                                   admin: PortalPerson) -> None:  # fmt: skip
    reviewer = staff_member(client, owner_engine, ["RS_REVIEWER"])
    code = {
        "code": "LOAN.RESTRUCTURE.OFFER",
        "display_text": {"en": "Loan restructuring offer", "ar": "عرض إعادة جدولة التمويل"},
        "max_priority": "NORMAL",
        "max_duration_min": 10,
        "allowed_channels": ["PRECALL_PUSH", "PSTN"],
    }
    r = client.post(f"{_t(tenant)}/purpose-codes", json=code, headers=admin.headers)
    assert r.status_code == 201 and r.json()["status"] == "PENDING_REVIEW"
    send = intent_body(tenant, purpose_code=code["code"], channel_preference=["PRECALL_PUSH", "PSTN"])
    assert (
        client.post(
            "/v1/intents", json=send, headers=auth(token_for(client, tenant), str(uuid4()))
        ).status_code
        == 422
    )
    r = client.post(
        f"{A}/review/purpose-codes/{tenant.tenant_id}/{code['code']}",
        json={"decision": "APPROVE", "reason": "Clear and specific"},
        headers=reviewer.headers,
    )
    assert r.status_code == 200 and r.json()["status"] == "APPROVED"
    assert (
        client.post(
            "/v1/intents", json=send, headers=auth(token_for(client, tenant), str(uuid4()))
        ).status_code
        == 201
    )
    r = client.post(f"{_t(tenant)}/purpose-codes/{code['code']}/retire", json={"reason": "Campaign ended"},
                    headers=admin.headers)  # fmt: skip
    assert r.json()["status"] == "RETIRED"
    assert (
        client.post(
            "/v1/intents", json=send, headers=auth(token_for(client, tenant), str(uuid4()))
        ).status_code
        == 422
    )


# Integration


def test_api_client_lifecycle(client: TestClient, owner_engine: Engine, tenant: SeededTenant) -> None:
    integ = portal_member(client, owner_engine, tenant, ["INTEGRATION_ADMIN"])
    r = client.post(
        f"{_t(tenant)}/api-clients",
        json={"label": "Core banking", "scopes": ["intents:write"]},
        headers=integ.headers,
    )
    assert r.status_code == 201 and r.headers["cache-control"] == "no-store"
    created = r.json()
    tok = client.post(
        "/oauth/token",
        data={"grant_type": "client_credentials", "client_id": created["client_id"],
              "client_secret": created["client_secret"]},
    )  # fmt: skip
    assert tok.status_code == 200 and tok.json()["scope"] == "intents:write"
    listed = client.get(f"{_t(tenant)}/api-clients", headers=integ.headers)
    assert created["client_secret"] not in listed.text and "secret_hash" not in listed.text
    r = client.post(f"{_t(tenant)}/api-clients/{created['client_id']}/revoke", json={"reason": "rotated"},
                    headers=integ.headers)  # fmt: skip
    assert r.json()["active"] is False
    tok = client.post(
        "/oauth/token",
        data={"grant_type": "client_credentials", "client_id": created["client_id"],
              "client_secret": created["client_secret"]},
    )  # fmt: skip
    assert tok.status_code == 401


def test_api_client_cannot_be_revoked_across_tenants(
    client: TestClient, owner_engine: Engine, tenant: SeededTenant, other_tenant: SeededTenant
) -> None:
    integ = portal_member(client, owner_engine, tenant, ["INTEGRATION_ADMIN"])
    r = client.post(f"{_t(tenant)}/api-clients/{other_tenant.client_id}/revoke", json={"reason": "attack"},
                    headers=integ.headers)  # fmt: skip
    assert r.status_code == 404
    assert token_for(client, other_tenant)


def test_api_client_limit(client: TestClient, owner_engine: Engine, tenant: SeededTenant) -> None:
    integ = portal_member(client, owner_engine, tenant, ["INTEGRATION_ADMIN"])
    saved = settings.api_clients_max_active
    settings.api_clients_max_active = 2  # seeded client is one
    try:
        body = {"label": "x", "scopes": ["intents:read"]}
        assert client.post(f"{_t(tenant)}/api-clients", json=body, headers=integ.headers).status_code == 201
        assert client.post(f"{_t(tenant)}/api-clients", json=body, headers=integ.headers).status_code == 409
    finally:
        settings.api_clients_max_active = saved


def test_webhooks_log_and_replay(
    client: TestClient, owner_engine: Engine, tenant: SeededTenant, clock: Clock
) -> None:
    integ = portal_member(client, owner_engine, tenant, ["INTEGRATION_ADMIN"])
    r = client.post(
        f"{_t(tenant)}/webhook-endpoints",
        json={
            "url": "https://hooks.mockbank.example/ringsays",
            "events": ["intent.delivered", "intent.cancelled"],
        },
        headers=integ.headers,
    )
    assert r.status_code == 201, r.text
    ep = r.json()
    assert ep["secret"].startswith("whsec_")
    assert ep["secret"] not in client.get(f"{_t(tenant)}/webhook-endpoints", headers=integ.headers).text
    bad = client.post(
        f"{_t(tenant)}/webhook-endpoints",
        json={"url": "https://10.0.0.5/hook", "events": ["intent.delivered"]},
        headers=integ.headers,
    )
    assert bad.status_code == 422
    user = AppUser(client, _phone())
    _send_intent(client, tenant, user.phone)
    delivery.deliver_due(clock.now + timedelta(minutes=30), DbRecipientDirectory(), MockPushSender())
    log = client.get(
        f"{_t(tenant)}/webhook-endpoints/{ep['endpoint_id']}/deliveries", headers=integ.headers
    ).json()
    assert [d["event_type"] for d in log["items"]] == ["intent.delivered"]
    d = log["items"][0]
    assert d["status"] == "PENDING"
    replay = f"{_t(tenant)}/webhook-deliveries/{d['delivery_id']}/replay"
    assert client.post(replay, headers=integ.headers).status_code == 409
    sender = webhooks.MockHttpSender()
    webhooks.dispatch_due(clock.now + timedelta(minutes=31), sender)
    log = client.get(
        f"{_t(tenant)}/webhook-endpoints/{ep['endpoint_id']}/deliveries?status=DELIVERED",
        headers=integ.headers,
    ).json()
    assert len(log["items"]) == 1
    r = client.post(replay, headers=integ.headers)
    assert r.status_code == 202 and r.json()["status"] == "PENDING"
    r = client.post(f"{_t(tenant)}/webhook-endpoints/{ep['endpoint_id']}/disable", json={"reason": "moved"},
                    headers=integ.headers)  # fmt: skip
    assert r.json()["active"] is False


def test_other_tenant_cannot_see_deliveries(
    client: TestClient, owner_engine: Engine, tenant: SeededTenant, other_tenant: SeededTenant
) -> None:
    integ = portal_member(client, owner_engine, tenant, ["INTEGRATION_ADMIN"])
    other = portal_member(client, owner_engine, other_tenant, ["INTEGRATION_ADMIN"])
    ep = client.post(
        f"{_t(tenant)}/webhook-endpoints",
        json={"url": "https://hooks.mockbank.example/a", "events": ["intent.delivered"]},
        headers=integ.headers,
    ).json()
    r = client.get(
        f"{_t(other_tenant)}/webhook-endpoints/{ep['endpoint_id']}/deliveries", headers=other.headers
    )
    assert r.status_code == 404


# Monitor


def test_monitor_masks_filters_and_scopes_agents(
    client: TestClient, owner_engine: Engine, tenant: SeededTenant, admin: PortalPerson, clock: Clock
) -> None:
    h = admin.headers
    client.post(
        f"{_t(tenant)}/agents",
        json={"agent_id": "agt_two", "display_name": {"en": "Two", "ar": "اثنان"},
              "department_id": str(tenant.department_id)},
        headers=h,
    )  # fmt: skip
    phone = _phone()
    mine = _send_intent(client, tenant, phone)
    other = _send_intent(client, tenant, agent_id="agt_two")
    page = client.get(f"{_t(tenant)}/intents", headers=h).json()
    ids = [i["intent_id"] for i in page["items"]]
    assert mine in ids and other in ids
    assert phone not in client.get(f"{_t(tenant)}/intents", headers=h).text
    item = next(i for i in page["items"] if i["intent_id"] == mine)
    assert item["to_masked"] == phone[:5] + "•••••" + phone[-3:]
    by_phone = client.post(f"{_t(tenant)}/intents/search", json={"phone": phone}, headers=h).json()["items"]
    assert [i["intent_id"] for i in by_phone] == [mine]
    by_status = client.get(f"{_t(tenant)}/intents?status=REQUESTED,CANCELLED", headers=h).json()["items"]
    assert len(by_status) == 2
    assert client.get(f"{_t(tenant)}/intents?status=NOPE", headers=h).status_code == 400
    agent = portal_member(client, owner_engine, tenant, ["AGENT"], agent_id=tenant.agent_id)
    seen = [
        i["intent_id"] for i in client.get(f"{_t(tenant)}/intents", headers=agent.headers).json()["items"]
    ]
    assert seen == [mine]
    assert client.get(f"{_t(tenant)}/intents/{other}", headers=agent.headers).status_code == 404
    detail = client.get(f"{_t(tenant)}/intents/{mine}", headers=h).json()
    assert detail["events"][0]["to_status"] == "REQUESTED"
    s = client.get(f"{_t(tenant)}/intents/summary", headers=h).json()
    assert s["total"] == 2 and s["by_status"] == {"REQUESTED": 2}
    assert client.get(f"{_t(tenant)}/intents/summary", headers=agent.headers).json()["total"] == 1


def test_monitor_paging(client: TestClient, tenant: SeededTenant, admin: PortalPerson) -> None:
    ids = {_send_intent(client, tenant) for _ in range(5)}
    seen: list[str] = []
    cursor = None
    while True:
        params: dict[str, Any] = {"limit": 2}
        if cursor:
            params["cursor"] = cursor
        page = client.get(f"{_t(tenant)}/intents", params=params, headers=admin.headers).json()
        seen += [i["intent_id"] for i in page["items"]]
        cursor = page["next_cursor"]
        if not cursor:
            break
    assert set(seen) == ids and len(seen) == 5


# Users


def test_user_admin_rules(
    client: TestClient, owner_engine: Engine, tenant: SeededTenant, admin: PortalPerson
) -> None:
    h = admin.headers
    email = unique_email("new")
    r = client.post(f"{_t(tenant)}/users", json={"email": email.upper(), "roles": ["SUPERVISOR"]}, headers=h)
    assert r.status_code == 201 and r.json()["status"] == "INVITED" and r.json()["email"] == email
    # Inviting again before acceptance renews the invitation.
    renewed = client.post(f"{_t(tenant)}/users", json={"email": email, "roles": ["SUPERVISOR"]}, headers=h)
    assert renewed.status_code == 201 and renewed.json()["user_id"] == r.json()["user_id"]
    no_agent = client.post(
        f"{_t(tenant)}/users", json={"email": unique_email(), "roles": ["AGENT"]}, headers=h
    )
    assert no_agent.status_code == 422
    me_id = next(u["user_id"] for u in client.get(f"{_t(tenant)}/users", headers=h).json()["items"]
                 if u["email"] == admin.email)  # fmt: skip
    assert (
        client.patch(f"{_t(tenant)}/users/{me_id}", json={"status": "DISABLED"}, headers=h).status_code == 409
    )
    assert (
        client.patch(f"{_t(tenant)}/users/{me_id}", json={"roles": ["SUPERVISOR"]}, headers=h).status_code
        == 409
    )
    invited_id = r.json()["user_id"]
    assert (
        client.patch(f"{_t(tenant)}/users/{invited_id}", json={"status": "ACTIVE"}, headers=h).status_code
        == 409
    )
    # Second admin can be removed; the last one cannot.
    second = portal_member(client, owner_engine, tenant, ["TENANT_ADMIN"])
    second_id = next(u["user_id"] for u in client.get(f"{_t(tenant)}/users", headers=h).json()["items"]
                     if u["email"] == second.email)  # fmt: skip
    r = client.patch(f"{_t(tenant)}/users/{me_id}", json={"roles": ["SUPERVISOR"]}, headers=second.headers)
    assert r.status_code == 200
    r = client.patch(
        f"{_t(tenant)}/users/{second_id}", json={"roles": ["SUPERVISOR"]}, headers=second.headers
    )
    assert r.status_code == 409


def test_invited_person_signs_in(client: TestClient, tenant: SeededTenant, admin: PortalPerson) -> None:
    email = unique_email("joiner")
    client.post(f"{_t(tenant)}/users", json={"email": email, "roles": ["COMPLIANCE"]}, headers=admin.headers)
    p = PortalPerson(client, email)
    assert p.me["memberships"][0]["roles"] == ["COMPLIANCE"]
    assert client.get(f"{_t(tenant)}/audit-events", headers=p.headers).status_code == 200


# Audit


def test_audit_list_export_and_formula_guard(
    client: TestClient, tenant: SeededTenant, admin: PortalPerson
) -> None:
    client.post(
        f"{_t(tenant)}/calling-numbers",
        json={"phone": "+966119999999", "cst_entity_name_registered": False},
        headers=admin.headers,
    )
    nums = client.get(f"{_t(tenant)}/calling-numbers", headers=admin.headers).json()["items"]
    pending = next(n for n in nums if n["status"] == "PENDING_VERIFICATION")
    client.post(f"{_t(tenant)}/calling-numbers/{pending['number_id']}/revoke",
                json={"reason": "=HYPERLINK(\"http://evil\")"}, headers=admin.headers)  # fmt: skip
    events = client.get(
        f"{_t(tenant)}/audit-events?action=calling_number.revoke", headers=admin.headers
    ).json()
    assert len(events["items"]) == 1
    csv_text = client.get(f"{_t(tenant)}/audit-events/export", headers=admin.headers).text
    assert "'=HYPERLINK" in csv_text and "\n=HYPERLINK" not in csv_text
    assert csv_text.splitlines()[0].startswith("event_id,at,actor")


def test_every_admin_write_is_audited(client: TestClient, tenant: SeededTenant, admin: PortalPerson) -> None:
    before = client.get(f"{_t(tenant)}/audit-events?limit=200", headers=admin.headers).json()["items"]
    client.post(
        f"{_t(tenant)}/departments", json={"name": {"en": "Ops", "ar": "العمليات"}}, headers=admin.headers
    )
    after = client.get(f"{_t(tenant)}/audit-events?limit=200", headers=admin.headers).json()["items"]
    assert len(after) == len(before) + 1
    assert after[0]["action"] == "department.create"
    assert after[0]["actor"].startswith("portal:")
