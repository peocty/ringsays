"""Regression tests for defects found in the independent stage 5 review (admin API and back office)."""

from __future__ import annotations

import csv
import io
import threading
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine
from sqlalchemy.exc import DBAPIError

from app.core.config import Settings, settings
from app.core.db import tenant_tx
from app.modules.admin import access, org
from app.modules.audit import service as audit
from app.platform import blobs
from app.scripts.seed import SeededTenant, invite_portal_user, seed_tenant

from .conftest import PortalPerson, portal_member, staff_member, unique_email

pytestmark = pytest.mark.integration
A = "/admin/v1"
PDF = b"%PDF-1.4\n%evidence\n%%EOF\n"
KINDS = ("COMMERCIAL_REGISTRATION", "REGULATOR_LICENCE", "AUTHORISATION_LETTER")


def _t(t: SeededTenant) -> str:
    return f"{A}/tenants/{t.tenant_id}"


def _pending(client: TestClient, owner_engine: Engine) -> tuple[SeededTenant, PortalPerson]:
    t = seed_tenant(owner_engine, verification_status="PENDING")
    p = portal_member(client, owner_engine, t, ["TENANT_ADMIN"])
    client.patch(
        _t(t), json={"commercial_registration": "1010000009", "domain": "bank.example"}, headers=p.headers
    )
    for kind in KINDS:
        r = client.post(
            f"{_t(t)}/verification/documents",
            data={"kind": kind},
            files={"file": (f"{kind}.pdf", PDF, "application/pdf")},
            headers=p.headers,
        )
        assert r.status_code == 201, r.text
    return t, p


def _app_conn(database: dict[str, str], tenant_id: UUID) -> Any:
    eng = create_engine(database["app"])
    conn = eng.connect()
    conn.execute(text("SELECT set_config('app.tenant_id', :t, false)"), {"t": str(tenant_id)})
    return eng, conn


# 1. Profile cannot change once verified, even if authorisation was read while pending


def test_profile_update_rechecks_status_under_lock(client: TestClient, owner_engine: Engine) -> None:
    t, p = _pending(client, owner_engine)
    member = access.load_member(__import__("app.core.oidc", fromlist=["verify"]).verify(p.token), t.tenant_id)
    assert member.tenant_status == "PENDING"
    with owner_engine.begin() as c:
        c.execute(text("SELECT set_config('app.tenant_id', :t, true)"), {"t": str(t.tenant_id)})
        c.execute(
            text("UPDATE enterprise.tenants SET verification_status='VERIFIED' WHERE id=:t"),
            {"t": t.tenant_id},
        )
    with pytest.raises(access.Conflict):
        with tenant_tx(t.tenant_id) as conn:
            org.update_tenant(
                conn, member, {"legal_name": {"en": "Other Bank", "ar": "بنك آخر"}}, datetime.now()
            )


def test_database_refuses_profile_change_after_verification(
    database: dict[str, str], tenant: SeededTenant
) -> None:
    eng, conn = _app_conn(database, tenant.tenant_id)
    try:
        with pytest.raises(DBAPIError):
            conn.execute(text("UPDATE enterprise.tenants SET legal_name_en = 'Spoof Bank'"))
    finally:
        conn.close()
        eng.dispose()


def test_database_refuses_profile_change_under_review(
    client: TestClient, owner_engine: Engine, database: dict[str, str]
) -> None:
    t, p = _pending(client, owner_engine)
    assert client.post(f"{_t(t)}/verification/submit", headers=p.headers).status_code == 201
    eng, conn = _app_conn(database, t.tenant_id)
    try:
        with pytest.raises(DBAPIError):
            conn.execute(text("UPDATE enterprise.tenants SET domain = 'other.example'"))
    finally:
        conn.close()
        eng.dispose()


# 2. Evidence is locked during review; approval needs all referenced evidence


def test_evidence_locked_in_database_while_under_review(
    client: TestClient, owner_engine: Engine, database: dict[str, str]
) -> None:
    t, p = _pending(client, owner_engine)
    assert client.post(f"{_t(t)}/verification/submit", headers=p.headers).status_code == 201
    eng, conn = _app_conn(database, t.tenant_id)
    try:
        with pytest.raises(DBAPIError):
            conn.execute(text("DELETE FROM enterprise.verification_documents"))
    finally:
        conn.close()
        eng.dispose()


def test_approval_refused_when_evidence_missing(client: TestClient, owner_engine: Engine) -> None:
    t, p = _pending(client, owner_engine)
    rid = client.post(f"{_t(t)}/verification/submit", headers=p.headers).json()["request_id"]
    with owner_engine.begin() as c:  # simulates evidence removed by a race before the guard existed
        c.execute(text("SELECT set_config('app.tenant_id', :t, true)"), {"t": str(t.tenant_id)})
        c.execute(
            text(
                "DELETE FROM enterprise.verification_documents "
                "WHERE tenant_id=:t AND kind='REGULATOR_LICENCE'"
            ),
            {"t": t.tenant_id},
        )
    reviewer = staff_member(client, owner_engine, ["RS_REVIEWER"])
    r = client.post(
        f"{A}/review/verifications/{rid}",
        json={"decision": "APPROVE", "reason": "looks fine"},
        headers=reviewer.headers,
    )
    assert r.status_code == 409
    assert client.get(_t(t), headers=p.headers).json()["verification_status"] == "PENDING"


def test_delete_racing_submit_cannot_remove_submitted_evidence(
    client: TestClient, owner_engine: Engine
) -> None:
    t, p = _pending(client, owner_engine)
    docs = client.get(f"{_t(t)}/verification", headers=p.headers).json()["documents"]
    results: list[int] = []

    def submit() -> None:
        results.append(client.post(f"{_t(t)}/verification/submit", headers=p.headers).status_code)

    def delete() -> None:
        results.append(
            client.delete(
                f"{_t(t)}/verification/documents/{docs[0]['document_id']}", headers=p.headers
            ).status_code
        )

    threads = [threading.Thread(target=submit), threading.Thread(target=delete)]
    for th in threads:
        th.start()
    for th in threads:
        th.join()
    v = client.get(f"{_t(t)}/verification", headers=p.headers).json()
    if v["latest_request"] and v["latest_request"]["status"] == "SUBMITTED":
        # Submitted: every referenced document still exists.
        assert len(v["documents"]) == 3
    else:
        assert sorted(results) == [204, 422]


# 3. Guard applies to any role except owner and back office (allow list)


def test_guard_applies_to_member_roles_of_app(database: dict[str, str], tenant: SeededTenant) -> None:
    admin = create_engine(
        __import__("tests.integration.conftest", fromlist=["ADMIN_URL"]).ADMIN_URL,
        isolation_level="AUTOCOMMIT",
    )
    role = f"ringsays_api_{uuid4().hex[:6]}"
    with admin.connect() as c:
        c.execute(text(f"CREATE ROLE {role} LOGIN PASSWORD 'x' IN ROLE ringsays_app"))
    try:
        url = database["app"].replace("ringsays_app:ringsays_app", f"{role}:x")
        eng = create_engine(url)
        for stmt in [
            "UPDATE enterprise.calling_numbers SET status = 'PENDING_VERIFICATION'",
            "INSERT INTO enterprise.purpose_codes (tenant_id, code, display_en, display_ar, max_priority, "
            "max_duration_min, allowed_channels, status) VALUES (platform.current_tenant(), 'X.Y', 'x', 'x', "
            "'LOW', 5, ARRAY['PSTN'], 'APPROVED')",
        ]:
            with pytest.raises(DBAPIError):
                with eng.begin() as c:
                    c.execute(
                        text("SELECT set_config('app.tenant_id', :t, true)"), {"t": str(tenant.tenant_id)}
                    )
                    c.execute(text(stmt))
        eng.dispose()
    finally:
        with admin.connect() as c:
            c.execute(text(f"DROP ROLE {role}"))
        admin.dispose()


# 4. Body size limited before authentication


def test_oversized_upload_refused_before_auth(client: TestClient, tenant: SeededTenant) -> None:
    big = b"%PDF-" + b"0" * (6 * 1024 * 1024)
    r = client.post(
        f"{_t(tenant)}/verification/documents",
        data={"kind": "OTHER"},
        files={"file": ("x.pdf", big, "application/pdf")},
    )
    assert r.status_code == 413


def test_streamed_body_without_length_is_counted(client: TestClient) -> None:
    def chunks() -> Any:
        for _ in range(40):
            yield b"x" * 65536

    r = client.post(f"{A}/backoffice/tenants", content=chunks(), headers={"Content-Type": "application/json"})
    assert r.status_code == 413


def test_json_body_limit(client: TestClient, tenant: SeededTenant) -> None:
    r = client.post(
        f"{_t(tenant)}/departments",
        content=b"{" + b" " * (2 * 1024 * 1024) + b"}",
        headers={"Content-Type": "application/json"},
    )
    assert r.status_code == 413


# 5. Phone search never in a URL


def test_phone_search_by_post_only(client: TestClient, owner_engine: Engine, tenant: SeededTenant) -> None:
    from .conftest import auth, intent_body, token_for

    admin = portal_member(client, owner_engine, tenant, ["TENANT_ADMIN"])
    phone = f"+9665{uuid4().int % 10**8:08d}"
    body = intent_body(tenant, to={"phone": phone}, channel_preference=["PRECALL_PUSH", "PSTN"])
    mine = client.post("/v1/intents", json=body, headers=auth(token_for(client, tenant), str(uuid4()))).json()
    client.post(
        "/v1/intents",
        json=intent_body(tenant, channel_preference=["PSTN"]),
        headers=auth(token_for(client, tenant), str(uuid4())),
    )
    found = client.post(f"{_t(tenant)}/intents/search", json={"phone": phone}, headers=admin.headers).json()[
        "items"
    ]
    assert [i["intent_id"] for i in found] == [mine["intent_id"]]
    # GET does not take a phone filter, so nobody is tempted to put a number in a URL.
    listed = client.get(f"{_t(tenant)}/intents", params={"phone": phone}, headers=admin.headers).json()[
        "items"
    ]
    assert len(listed) == 2


# 6. Invitations bind only for the tenant identity provider


def test_staff_issuer_cannot_consume_tenant_invitation(
    client: TestClient, owner_engine: Engine, tenant: SeededTenant
) -> None:
    email = unique_email("victim")
    invite_portal_user(owner_engine, tenant.tenant_id, email, ["TENANT_ADMIN"])
    saved = settings.admin_oidc_issuer
    settings.admin_oidc_issuer = "https://tenant-idp.example"  # MOCK issuer now acts as staff issuer only
    try:
        p = PortalPerson(client, email)
        assert p.me["memberships"] == []
    finally:
        settings.admin_oidc_issuer = saved
    with owner_engine.connect() as c:
        status = c.execute(
            text("SELECT status FROM enterprise.portal_users WHERE email=:e"), {"e": email}
        ).scalar_one()
    assert status == "INVITED"


# 7. Failed upload leaves no blob behind


def test_failed_upload_removes_blob(
    client: TestClient, owner_engine: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    t = seed_tenant(owner_engine, verification_status="PENDING")
    p = portal_member(client, owner_engine, t, ["TENANT_ADMIN"])
    store = blobs.get_store()
    assert isinstance(store, blobs.LocalBlobStore)
    before = list(Path(store.root).rglob("*"))

    def boom(*_: Any, **__: Any) -> None:
        raise RuntimeError("audit down")

    monkeypatch.setattr(org, "_audit", boom)
    with pytest.raises(RuntimeError):
        client.post(
            f"{_t(t)}/verification/documents",
            data={"kind": "OTHER"},
            files={"file": ("e.pdf", PDF, "application/pdf")},
            headers=p.headers,
        )
    after = [f for f in Path(store.root).rglob("*") if f.is_file()]
    assert after == [f for f in before if f.is_file()]


# 8. Nobody changes their own roles; integration admin cannot mint catalogue:write


def test_admin_cannot_self_grant_roles(
    client: TestClient, owner_engine: Engine, tenant: SeededTenant
) -> None:
    admin = portal_member(client, owner_engine, tenant, ["TENANT_ADMIN"])
    me = next(
        u
        for u in client.get(f"{_t(tenant)}/users", headers=admin.headers).json()["items"]
        if u["email"] == admin.email
    )
    r = client.patch(
        f"{_t(tenant)}/users/{me['user_id']}",
        json={"roles": ["TENANT_ADMIN", "INTEGRATION_ADMIN"]},
        headers=admin.headers,
    )
    assert r.status_code == 409


def test_integration_admin_cannot_mint_catalogue_write(
    client: TestClient, owner_engine: Engine, tenant: SeededTenant
) -> None:
    integ = portal_member(client, owner_engine, tenant, ["INTEGRATION_ADMIN"])
    r = client.post(
        f"{_t(tenant)}/api-clients", json={"label": "x", "scopes": ["catalogue:write"]}, headers=integ.headers
    )
    assert r.status_code == 403


# 9. Containment works while suspended


def test_containment_allowed_while_suspended(
    client: TestClient, owner_engine: Engine, tenant: SeededTenant
) -> None:
    integ = portal_member(client, owner_engine, tenant, ["INTEGRATION_ADMIN"])
    admin = portal_member(client, owner_engine, tenant, ["TENANT_ADMIN"])
    sup = portal_member(client, owner_engine, tenant, ["SUPERVISOR"])
    created = client.post(
        f"{_t(tenant)}/api-clients", json={"label": "x", "scopes": ["intents:read"]}, headers=integ.headers
    ).json()
    ep = client.post(
        f"{_t(tenant)}/webhook-endpoints",
        json={"url": "https://hooks.mockbank.example/a", "events": ["intent.delivered"]},
        headers=integ.headers,
    ).json()
    ops = staff_member(client, owner_engine, ["RS_ADMIN"])
    client.post(
        f"{A}/backoffice/tenants/{tenant.tenant_id}/suspend", json={"reason": "leak"}, headers=ops.headers
    )
    assert (
        client.post(
            f"{_t(tenant)}/api-clients/{created['client_id']}/revoke",
            json={"reason": "leaked"},
            headers=integ.headers,
        ).status_code
        == 200
    )
    assert (
        client.post(
            f"{_t(tenant)}/webhook-endpoints/{ep['endpoint_id']}/disable",
            json={"reason": "leaked"},
            headers=integ.headers,
        ).status_code
        == 200
    )
    sup_id = next(
        u["user_id"]
        for u in client.get(f"{_t(tenant)}/users", headers=admin.headers).json()["items"]
        if u["email"] == sup.email
    )
    assert (
        client.patch(
            f"{_t(tenant)}/users/{sup_id}", json={"status": "DISABLED"}, headers=admin.headers
        ).status_code
        == 200
    )
    assert (
        client.patch(
            f"{_t(tenant)}/users/{sup_id}", json={"roles": ["COMPLIANCE"]}, headers=admin.headers
        ).status_code
        == 403
    )
    assert (
        client.post(
            f"{_t(tenant)}/api-clients",
            json={"label": "y", "scopes": ["intents:read"]},
            headers=integ.headers,
        ).status_code
        == 403
    )


# 10. Expired invitations can be renewed; concurrent invites never give 500


def test_expired_invitation_renewed_by_reinvite(
    client: TestClient, owner_engine: Engine, tenant: SeededTenant, clock: Any
) -> None:
    admin = portal_member(client, owner_engine, tenant, ["TENANT_ADMIN"])
    email = unique_email("late")
    client.post(f"{_t(tenant)}/users", json={"email": email, "roles": ["SUPERVISOR"]}, headers=admin.headers)
    with owner_engine.begin() as c:
        c.execute(
            text(
                "UPDATE enterprise.portal_users SET invite_expires_at = now() - interval '1 day' "
                "WHERE email=:e"
            ),
            {"e": email},
        )
    assert PortalPerson(client, email).me["memberships"] == []
    clock.now = datetime.now().astimezone()
    r = client.post(
        f"{_t(tenant)}/users", json={"email": email, "roles": ["COMPLIANCE"]}, headers=admin.headers
    )
    assert r.status_code == 201 and r.json()["roles"] == ["COMPLIANCE"], r.text
    assert PortalPerson(client, email).me["memberships"][0]["roles"] == ["COMPLIANCE"]
    again = client.post(
        f"{_t(tenant)}/users", json={"email": email, "roles": ["COMPLIANCE"]}, headers=admin.headers
    )
    assert again.status_code == 409  # accepted members cannot be re-invited


def test_concurrent_invites_no_500(client: TestClient, owner_engine: Engine, tenant: SeededTenant) -> None:
    admin = portal_member(client, owner_engine, tenant, ["TENANT_ADMIN"])
    email = unique_email("race")
    codes: list[int] = []

    def go() -> None:
        codes.append(
            client.post(
                f"{_t(tenant)}/users", json={"email": email, "roles": ["SUPERVISOR"]}, headers=admin.headers
            ).status_code
        )

    threads = [threading.Thread(target=go) for _ in range(4)]
    for th in threads:
        th.start()
    for th in threads:
        th.join()
    assert set(codes) <= {201, 409} and 500 not in codes


# 11. Audit covers invitation acceptance and staff evidence downloads


def test_acceptance_and_download_are_audited(client: TestClient, owner_engine: Engine) -> None:
    t, p = _pending(client, owner_engine)
    events = client.get(f"{_t(t)}/audit-events?limit=200", headers=p.headers).json()["items"]
    assert "portal_user.accept_invitation" in [e["action"] for e in events]
    rid = client.post(f"{_t(t)}/verification/submit", headers=p.headers).json()["request_id"]
    reviewer = staff_member(client, owner_engine, ["RS_REVIEWER"])
    doc = client.get(f"{A}/review/verifications/{rid}", headers=reviewer.headers).json()["documents"][0]
    assert (
        client.get(f"{A}/review/documents/{doc['document_id']}/file", headers=reviewer.headers).status_code
        == 200
    )
    events = client.get(
        f"{_t(t)}/audit-events?action=verification.document_download", headers=p.headers
    ).json()
    assert events["items"][0]["actor"].startswith("staff:")


# 12. NUL characters are a 400, not a 500


def test_nul_characters_rejected(client: TestClient, owner_engine: Engine, tenant: SeededTenant) -> None:
    admin = portal_member(client, owner_engine, tenant, ["TENANT_ADMIN"])
    assert client.get(f"{_t(tenant)}/intents?agent_id=a%00b", headers=admin.headers).status_code == 400
    r = client.post(
        f"{_t(tenant)}/departments", json={"name": {"en": "a\u0000b", "ar": "س"}}, headers=admin.headers
    )
    assert r.status_code == 400


# 13. CSV export can be verified independently


def test_audit_csv_recomputes_hash_chain(
    client: TestClient, owner_engine: Engine, tenant: SeededTenant
) -> None:
    admin = portal_member(client, owner_engine, tenant, ["TENANT_ADMIN"])
    n = client.post(
        f"{_t(tenant)}/calling-numbers",
        json={"phone": "+966114440000", "cst_entity_name_registered": False},
        headers=admin.headers,
    ).json()
    client.post(
        f"{_t(tenant)}/calling-numbers/{n['number_id']}/revoke",
        json={"reason": "'=not a formula"},
        headers=admin.headers,
    )
    rows = list(
        csv.DictReader(
            io.StringIO(client.get(f"{_t(tenant)}/audit-events/export", headers=admin.headers).text)
        )
    )
    prev = audit.GENESIS
    for r in rows:
        for f in r["escaped_fields"].split():
            r[f] = r[f][1:]
        digest = audit.compute_hash(
            prev,
            UUID(r["event_id"]),
            tenant.tenant_id,
            datetime.fromisoformat(r["at"]),
            r["actor"],
            r["action"],
            r["object_type"],
            r["object_id"],
            r["reason"] or None,
        )
        assert r["prev_hash"] == prev and r["hash"] == digest
        prev = digest
    assert rows[-1]["reason"] == "'=not a formula"


# 14. Settings fail closed


def test_settings_default_to_production(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.delenv("RINGSAYS_ENVIRONMENT", raising=False)
    monkeypatch.chdir(tmp_path)  # no .env
    s = Settings()
    assert s.environment == "production" and s.dev_oidc_enabled is False
    with pytest.raises(RuntimeError):
        s.assert_safe_for_environment()


def test_production_refuses_local_blob_storage(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.chdir(tmp_path)
    s = Settings(
        environment="production",
        jwt_secret="x" * 40,
        webhook_secret_key="Zm9vYmFyYmF6cXV4cXV1eHF1dXhxdXV4cXV1eHF1dXg=",  # noqa: S106
        phone_pepper="p",
        admin_oidc_issuer="https://idp.example",
        staff_oidc_issuer="https://staff.example",
        backoffice_enabled=False,
        use_mock_adapters=False,
    )
    with pytest.raises(RuntimeError, match="BLOB"):
        s.assert_safe_for_environment()


def test_refresh_token_rotates(client: TestClient) -> None:
    import base64
    import hashlib
    import secrets
    from urllib.parse import parse_qs, urlsplit

    verifier = secrets.token_urlsafe(48)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    redirect = settings.portal_redirect_uris[0]
    r = client.post(
        "/dev/oidc/authorize",
        data={
            "redirect_uri": redirect,
            "state": "s",
            "code_challenge": challenge,
            "email": "x@mockbank.example",
        },
        follow_redirects=False,
    )
    code = parse_qs(urlsplit(r.headers["location"]).query)["code"][0]
    tok = client.post(
        "/dev/oidc/token",
        data={
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": redirect,
            "client_id": "ringsays-portal",
            "code_verifier": verifier,
        },
    ).json()
    first = client.post(
        "/dev/oidc/token",
        data={
            "grant_type": "refresh_token",
            "refresh_token": tok["refresh_token"],
            "client_id": "ringsays-portal",
        },
    )
    assert first.status_code == 200 and first.json()["refresh_token"] != tok["refresh_token"]
    reused = client.post(
        "/dev/oidc/token",
        data={
            "grant_type": "refresh_token",
            "refresh_token": tok["refresh_token"],
            "client_id": "ringsays-portal",
        },
    )
    assert reused.status_code == 400
    _ = timedelta
