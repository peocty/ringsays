"""Admin API routes for the enterprise portal (contracts/openapi/admin.yaml)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Annotated, Any, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, File, Form, Path, Query, Response, UploadFile
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, ConfigDict, EmailStr, Field, StringConstraints
from sqlalchemy import select, text
from starlette.concurrency import run_in_threadpool

from app.core.config import settings
from app.core.db import anonymous_tx, tenant_tx
from app.core.tables import tenants
from app.modules.audit import service as audit
from app.modules.intent.domain import Channel, IntentStatus, Priority
from app.platform import blobs

from . import integration, monitor, org
from .access import BadRequest, Forbidden, IdentityDep, Member, Perm, load_staff, need, permissions_for

router = APIRouter(prefix="/admin/v1", tags=["Admin"])


def clock() -> datetime:
    """Overridden in tests."""
    return datetime.now(UTC)


Now = Annotated[datetime, Depends(clock)]

Text160 = Annotated[str, StringConstraints(min_length=1, max_length=160, strip_whitespace=True)]
E164 = Annotated[str, StringConstraints(pattern=r"^\+[1-9]\d{6,14}$")]
TenantRole = Literal["TENANT_ADMIN", "INTEGRATION_ADMIN", "SUPERVISOR", "AGENT", "COMPLIANCE"]
Scope = Literal["intents:write", "intents:read", "catalogue:read", "catalogue:write"]
WebhookEvent = Literal[
    "intent.delivered",
    "intent.accepted",
    "intent.rescheduled",
    "intent.scheduled",
    "intent.declined",
    "intent.expired",
    "intent.cancelled",
    "outcome.recorded",
]
DocumentKind = Literal[
    "COMMERCIAL_REGISTRATION", "REGULATOR_LICENCE", "AUTHORISATION_LETTER", "DOMAIN_PROOF", "OTHER"
]


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Localised(_Strict):
    en: Text160
    ar: Text160


class ReasonIn(_Strict):
    reason: Annotated[str, StringConstraints(min_length=3, max_length=500, strip_whitespace=True)]


class TenantUpdateIn(_Strict):
    legal_name: Localised | None = None
    commercial_registration: Annotated[str, StringConstraints(pattern=r"^[0-9]{10}$")] | None = None
    domain: (
        Annotated[
            str,
            StringConstraints(
                max_length=253,
                pattern=r"^([A-Za-z0-9]([A-Za-z0-9-]{0,61}[A-Za-z0-9])?\.)+[A-Za-z]{2,63}$",
                to_lower=True,
            ),
        ]
        | None
    ) = None


class DepartmentIn(_Strict):
    name: Localised


class AgentIn(_Strict):
    agent_id: Annotated[str, StringConstraints(pattern=r"^[A-Za-z0-9_.-]{3,64}$")]
    display_name: Localised
    department_id: UUID
    employee_ref: Annotated[str, StringConstraints(max_length=64)] | None = None


class AgentUpdateIn(_Strict):
    display_name: Localised | None = None
    department_id: UUID | None = None
    employee_ref: Annotated[str, StringConstraints(max_length=64)] | None = None
    active: bool | None = None


class NumberIn(_Strict):
    phone: E164
    department_id: UUID | None = None
    cst_entity_name_registered: bool


class InviteIn(_Strict):
    email: Annotated[EmailStr, StringConstraints(max_length=254)]
    display_name: Annotated[str, StringConstraints(max_length=120)] | None = None
    roles: Annotated[list[TenantRole], Field(min_length=1, max_length=5)]
    agent_id: str | None = None


class UserUpdateIn(_Strict):
    roles: Annotated[list[TenantRole], Field(min_length=1, max_length=5)] | None = None
    agent_id: str | None = None
    status: Literal["ACTIVE", "DISABLED"] | None = None


class PurposeCodeIn(_Strict):
    code: Annotated[str, StringConstraints(pattern=r"^[A-Z][A-Z0-9]*(\.[A-Z0-9]+){1,4}$", max_length=64)]
    display_text: Localised
    max_priority: Priority
    max_duration_min: Annotated[int, Field(ge=1, le=120)]
    allowed_channels: Annotated[list[Channel], Field(min_length=1)]


class ApiClientIn(_Strict):
    label: Annotated[str, StringConstraints(min_length=1, max_length=80, strip_whitespace=True)]
    scopes: Annotated[list[Scope], Field(min_length=1, max_length=4)]


class WebhookIn(_Strict):
    url: Annotated[str, StringConstraints(pattern=r"^https://", max_length=2048)]
    events: Annotated[list[WebhookEvent], Field(min_length=1, max_length=8)]


def _set_fields(model: BaseModel) -> dict[str, Any]:
    """Only fields the caller sent (null included), so PATCH can clear a value."""
    return model.model_dump(mode="json", exclude_unset=True)


ReadOrg = Annotated[Member, Depends(need(Perm.ORG_READ))]
ManageOrg = Annotated[Member, Depends(need(Perm.ORG_MANAGE, write=True))]
ManageUsers = Annotated[Member, Depends(need(Perm.USERS_MANAGE, write=True))]
ReadUsers = Annotated[Member, Depends(need(Perm.USERS_MANAGE))]
ProposeCodes = Annotated[Member, Depends(need(Perm.CATALOGUE_PROPOSE, write=True))]
ReadIntegration = Annotated[Member, Depends(need(Perm.INTEGRATION_READ))]
ManageIntegration = Annotated[Member, Depends(need(Perm.INTEGRATION_MANAGE, write=True))]
ReadIntents = Annotated[Member, Depends(need(Perm.INTENTS_READ_ALL, Perm.INTENTS_READ_OWN))]
# Containment actions stay available while an organisation is suspended (revoke, disable, stop using).
ContainOrg = Annotated[Member, Depends(need(Perm.ORG_MANAGE))]
ContainIntegration = Annotated[Member, Depends(need(Perm.INTEGRATION_MANAGE))]
UsersAny = Annotated[Member, Depends(need(Perm.USERS_MANAGE))]
ReadAudit = Annotated[Member, Depends(need(Perm.AUDIT_READ))]
Limit = Annotated[int, Query(ge=1, le=200)]


# Session


@router.get("/me", tags=["Session"])
def me(identity: IdentityDep, now: Now) -> dict[str, Any]:
    rows: list[Any] = []
    # Only identities from the tenant identity provider may accept tenant invitations.
    if identity.issuer == settings.admin_oidc_issuer:
        with anonymous_tx() as conn:
            rows = list(
                conn.execute(
                    text("SELECT * FROM enterprise.portal_sign_in(:i, :s, :e, :v, :n)"),
                    {
                        "i": identity.issuer,
                        "s": identity.subject,
                        "e": identity.email,
                        "v": identity.email_verified,
                        "n": now,
                    },
                ).all()
            )
    memberships = []
    for r in rows:
        with tenant_tx(r.tenant_id) as conn:
            t = conn.execute(select(tenants).where(tenants.c.id == r.tenant_id)).one()
            if r.newly_bound:
                audit.append(
                    conn,
                    tenant_id=r.tenant_id,
                    actor=f"portal:{r.user_id}",
                    action="portal_user.accept_invitation",
                    object_type="portal_user",
                    object_id=str(r.user_id),
                    at=now,
                )
        memberships.append(
            {
                "tenant_id": str(r.tenant_id),
                "legal_name": {"en": t.legal_name_en, "ar": t.legal_name_ar},
                "verification_status": t.verification_status,
                "roles": sorted(r.roles),
                "permissions": sorted(p.value for p in permissions_for(r.roles)),
                "agent_id": r.agent_id,
            }
        )
    staff = load_staff(identity, now)
    return {
        "email": identity.email or "",
        "display_name": identity.name,
        "memberships": memberships,
        "staff_roles": sorted(staff.roles) if staff else [],
    }


# Organisation


@router.get("/tenants/{tenant_id}", tags=["Organisation"])
def get_tenant(m: ReadOrg) -> dict[str, Any]:
    with tenant_tx(m.tenant_id) as conn:
        return org.get_tenant(conn, m.tenant_id)


@router.patch("/tenants/{tenant_id}", tags=["Organisation"])
def update_tenant(body: TenantUpdateIn, m: ManageOrg, now: Now) -> dict[str, Any]:
    fields = {k: v for k, v in _set_fields(body).items() if v is not None}
    if not fields:
        raise BadRequest("Nothing to update")
    with tenant_tx(m.tenant_id) as conn:
        return org.update_tenant(conn, m, fields, now)


@router.get("/tenants/{tenant_id}/departments", tags=["Organisation"])
def list_departments(m: ReadOrg) -> dict[str, Any]:
    with tenant_tx(m.tenant_id) as conn:
        return {"items": org.list_departments(conn)}


@router.post("/tenants/{tenant_id}/departments", status_code=201, tags=["Organisation"])
def create_department(body: DepartmentIn, m: ManageOrg, now: Now) -> dict[str, Any]:
    with tenant_tx(m.tenant_id) as conn:
        return org.create_department(conn, m, body.name.model_dump(), now)


@router.patch("/tenants/{tenant_id}/departments/{department_id}", tags=["Organisation"])
def update_department(department_id: UUID, body: DepartmentIn, m: ManageOrg, now: Now) -> dict[str, Any]:
    with tenant_tx(m.tenant_id) as conn:
        return org.update_department(conn, m, department_id, body.name.model_dump(), now)


@router.get("/tenants/{tenant_id}/agents", tags=["Organisation"])
def list_agents(m: ReadOrg, cursor: str | None = None, limit: Limit = 50) -> dict[str, Any]:
    with tenant_tx(m.tenant_id) as conn:
        return org.list_agents(conn, limit, cursor)


@router.post("/tenants/{tenant_id}/agents", status_code=201, tags=["Organisation"])
def create_agent(body: AgentIn, m: ManageOrg, now: Now) -> dict[str, Any]:
    with tenant_tx(m.tenant_id) as conn:
        return org.create_agent(conn, m, body.model_dump(mode="json"), now)


@router.patch("/tenants/{tenant_id}/agents/{agent_id}", tags=["Organisation"])
def update_agent(agent_id: str, body: AgentUpdateIn, m: ManageOrg, now: Now) -> dict[str, Any]:
    fields = _set_fields(body)
    for required in ("display_name", "department_id", "active"):
        if required in fields and fields[required] is None:
            raise BadRequest(f"{required} cannot be empty")
    if not fields:
        raise BadRequest("Nothing to update")
    with tenant_tx(m.tenant_id) as conn:
        return org.update_agent(conn, m, agent_id, fields, now)


@router.get("/tenants/{tenant_id}/calling-numbers", tags=["Organisation"])
def list_numbers(m: ReadOrg) -> dict[str, Any]:
    with tenant_tx(m.tenant_id) as conn:
        return {"items": org.list_numbers(conn)}


@router.post("/tenants/{tenant_id}/calling-numbers", status_code=201, tags=["Organisation"])
def add_number(body: NumberIn, m: ManageOrg, now: Now) -> dict[str, Any]:
    with tenant_tx(m.tenant_id) as conn:
        return org.add_number(conn, m, body.model_dump(mode="json"), now)


@router.post("/tenants/{tenant_id}/calling-numbers/{number_id}/revoke", tags=["Organisation"])
def revoke_number(number_id: UUID, body: ReasonIn, m: ContainOrg, now: Now) -> dict[str, Any]:
    with tenant_tx(m.tenant_id) as conn:
        return org.revoke_number(conn, m, number_id, body.reason, now)


# Verification


@router.get("/tenants/{tenant_id}/verification", tags=["Verification"])
def get_verification(m: ReadOrg) -> dict[str, Any]:
    with tenant_tx(m.tenant_id) as conn:
        return org.get_verification(conn, m.tenant_id)


@router.post("/tenants/{tenant_id}/verification/documents", status_code=201, tags=["Verification"])
async def upload_document(
    m: ManageOrg,
    now: Now,
    kind: Annotated[DocumentKind, Form()],
    file: Annotated[UploadFile, File()],
    reference: Annotated[str | None, Form(max_length=80)] = None,
) -> dict[str, Any]:
    data = await file.read(org.MAX_DOCUMENT_BYTES + 1)
    content_type = org.validate_document(data)

    # Object storage and database calls block: run them off the event loop, or one slow upload would
    # stall every other request on this process.
    def store_and_record() -> dict[str, Any]:
        store = blobs.get_store()
        key = store.put(data)
        try:
            with tenant_tx(m.tenant_id) as conn:
                return org.record_document(
                    conn, m, kind, reference, file.filename, data, content_type, key, now
                )
        except BaseException:
            store.delete(key)  # nothing committed refers to it
            raise

    return await run_in_threadpool(store_and_record)


@router.delete(
    "/tenants/{tenant_id}/verification/documents/{document_id}", status_code=204, tags=["Verification"]
)
def delete_document(document_id: UUID, m: ManageOrg, now: Now) -> Response:
    with tenant_tx(m.tenant_id) as conn:
        key = org.delete_document(conn, m, document_id, now)
    blobs.get_store().delete(key)
    return Response(status_code=204)


@router.post("/tenants/{tenant_id}/verification/submit", status_code=201, tags=["Verification"])
def submit_verification(m: ManageOrg, now: Now) -> dict[str, Any]:
    with tenant_tx(m.tenant_id) as conn:
        return org.submit_verification(conn, m, now)


# Users


@router.get("/tenants/{tenant_id}/users", tags=["Users"])
def list_users(m: ReadUsers) -> dict[str, Any]:
    with tenant_tx(m.tenant_id) as conn:
        return {"items": org.list_users(conn)}


@router.post("/tenants/{tenant_id}/users", status_code=201, tags=["Users"])
def invite_user(body: InviteIn, m: ManageUsers, now: Now) -> dict[str, Any]:
    with tenant_tx(m.tenant_id) as conn:
        return org.invite_user(conn, m, body.model_dump(mode="json"), now)


@router.patch("/tenants/{tenant_id}/users/{user_id}", tags=["Users"])
def update_user(user_id: UUID, body: UserUpdateIn, m: UsersAny, now: Now) -> dict[str, Any]:
    fields = _set_fields(body)
    if m.tenant_status == "SUSPENDED" and fields != {"status": "DISABLED"}:
        raise Forbidden("Organisation is suspended; only disabling people is allowed")
    for required in ("roles", "status"):
        if required in fields and fields[required] is None:
            raise BadRequest(f"{required} cannot be empty")
    if not fields:
        raise BadRequest("Nothing to update")
    with tenant_tx(m.tenant_id) as conn:
        return org.update_user(conn, m, user_id, fields, now)


# Catalogue


@router.get("/tenants/{tenant_id}/purpose-codes", tags=["Catalogue"])
def list_codes(m: ReadOrg) -> dict[str, Any]:
    with tenant_tx(m.tenant_id) as conn:
        return {"items": integration.list_codes(conn)}


@router.post("/tenants/{tenant_id}/purpose-codes", status_code=201, tags=["Catalogue"])
def propose_code(body: PurposeCodeIn, m: ProposeCodes, now: Now) -> dict[str, Any]:
    payload = body.model_dump(mode="json")
    payload["allowed_channels"] = list(dict.fromkeys(payload["allowed_channels"]))
    with tenant_tx(m.tenant_id) as conn:
        return integration.propose_code(conn, m, payload, now)


@router.post("/tenants/{tenant_id}/purpose-codes/{code}/retire", tags=["Catalogue"])
def retire_code(code: str, body: ReasonIn, m: ProposeCodes, now: Now) -> dict[str, Any]:
    with tenant_tx(m.tenant_id) as conn:
        return integration.retire_code(conn, m, code, body.reason, now)


# Integration


@router.get("/tenants/{tenant_id}/api-clients", tags=["Integration"])
def list_clients(m: ReadIntegration) -> dict[str, Any]:
    with tenant_tx(m.tenant_id) as conn:
        return {"items": integration.list_clients(conn)}


@router.post("/tenants/{tenant_id}/api-clients", status_code=201, tags=["Integration"])
def create_client(body: ApiClientIn, m: ManageIntegration, now: Now) -> JSONResponse:
    if "catalogue:write" in body.scopes and Perm.CATALOGUE_PROPOSE not in m.permissions:
        raise Forbidden("catalogue:write needs a role that may propose purpose codes")
    with tenant_tx(m.tenant_id) as conn:
        out = integration.create_client(conn, m, body.label, list(body.scopes), now)
    return JSONResponse(out, status_code=201, headers={"Cache-Control": "no-store"})


@router.post("/tenants/{tenant_id}/api-clients/{client_id}/revoke", tags=["Integration"])
def revoke_client(client_id: str, body: ReasonIn, m: ContainIntegration, now: Now) -> dict[str, Any]:
    with tenant_tx(m.tenant_id) as conn:
        return integration.revoke_client(conn, m, client_id, body.reason, now)


@router.get("/tenants/{tenant_id}/webhook-endpoints", tags=["Integration"])
def list_endpoints(m: ReadIntegration) -> dict[str, Any]:
    with tenant_tx(m.tenant_id) as conn:
        return {"items": integration.list_endpoints(conn)}


@router.post("/tenants/{tenant_id}/webhook-endpoints", status_code=201, tags=["Integration"])
def create_endpoint(body: WebhookIn, m: ManageIntegration, now: Now) -> JSONResponse:
    with tenant_tx(m.tenant_id) as conn:
        out = integration.create_endpoint(conn, m, body.url, list(dict.fromkeys(body.events)), now)
    return JSONResponse(out, status_code=201, headers={"Cache-Control": "no-store"})


@router.post("/tenants/{tenant_id}/webhook-endpoints/{endpoint_id}/disable", tags=["Integration"])
def disable_endpoint(endpoint_id: UUID, body: ReasonIn, m: ContainIntegration, now: Now) -> dict[str, Any]:
    with tenant_tx(m.tenant_id) as conn:
        return integration.disable_endpoint(conn, m, endpoint_id, body.reason, now)


@router.get("/tenants/{tenant_id}/webhook-endpoints/{endpoint_id}/deliveries", tags=["Integration"])
def list_deliveries(
    endpoint_id: UUID,
    m: ReadIntegration,
    status: Literal["PENDING", "SENDING", "DELIVERED", "DEAD"] | None = None,
    cursor: Annotated[int | None, Query(ge=1)] = None,
    limit: Limit = 50,
) -> dict[str, Any]:
    with tenant_tx(m.tenant_id) as conn:
        return integration.list_deliveries(conn, endpoint_id, status, limit, cursor)


@router.post(
    "/tenants/{tenant_id}/webhook-deliveries/{delivery_id}/replay", status_code=202, tags=["Integration"]
)
def replay_delivery(
    delivery_id: Annotated[int, Path(ge=1)], m: ManageIntegration, now: Now
) -> dict[str, Any]:
    with tenant_tx(m.tenant_id) as conn:
        return integration.replay_delivery(conn, m, delivery_id, now)


# Monitor


def _statuses(raw: list[str] | None) -> list[str] | None:
    if not raw:
        return None
    try:
        return [IntentStatus(x).value for x in raw if x]
    except ValueError as exc:
        raise BadRequest("status contains an unknown value") from exc


@router.get("/tenants/{tenant_id}/intents", tags=["Monitor"])
def list_intents(
    m: ReadIntents,
    status: Annotated[str | None, Query()] = None,
    purpose_code: str | None = None,
    agent_id: str | None = None,
    created_from: datetime | None = None,
    created_to: datetime | None = None,
    cursor: str | None = None,
    limit: Limit = 50,
) -> dict[str, Any]:
    with tenant_tx(m.tenant_id) as conn:
        return monitor.list_intents(
            conn,
            m,
            statuses=_statuses(status.split(",") if status else None),
            purpose_code=purpose_code,
            agent_id=agent_id,
            phone=None,
            created_from=created_from,
            created_to=created_to,
            limit=limit,
            cursor=cursor,
        )


class IntentSearchIn(_Strict):
    """Same filters as GET /intents plus phone. POST so the number never sits in a URL or access log."""

    status: Annotated[list[str], Field(max_length=12)] | None = None
    purpose_code: Annotated[str, StringConstraints(max_length=64)] | None = None
    agent_id: Annotated[str, StringConstraints(max_length=64)] | None = None
    phone: E164 | None = None
    created_from: datetime | None = None
    created_to: datetime | None = None
    cursor: Annotated[str, StringConstraints(max_length=200)] | None = None
    limit: Annotated[int, Field(ge=1, le=200)] = 50


@router.post("/tenants/{tenant_id}/intents/search", tags=["Monitor"])
def search_intents(body: IntentSearchIn, m: ReadIntents) -> dict[str, Any]:
    with tenant_tx(m.tenant_id) as conn:
        return monitor.list_intents(
            conn,
            m,
            statuses=_statuses(body.status),
            purpose_code=body.purpose_code,
            agent_id=body.agent_id,
            phone=body.phone,
            created_from=body.created_from,
            created_to=body.created_to,
            limit=body.limit,
            cursor=body.cursor,
        )


@router.get("/tenants/{tenant_id}/intents/summary", tags=["Monitor"])
def intents_summary(m: ReadIntents, now: Now, since: datetime | None = None) -> dict[str, Any]:
    with tenant_tx(m.tenant_id) as conn:
        return monitor.summary(conn, m, since or now - timedelta(hours=24))


@router.get("/tenants/{tenant_id}/intents/{intent_id}", tags=["Monitor"])
def intent_detail(intent_id: UUID, m: ReadIntents) -> dict[str, Any]:
    with tenant_tx(m.tenant_id) as conn:
        return monitor.intent_detail(conn, m, intent_id)


# Audit


@router.get("/tenants/{tenant_id}/audit-events", tags=["Audit"])
def list_audit(
    m: ReadAudit, action: str | None = None, cursor: str | None = None, limit: Limit = 50
) -> dict[str, Any]:
    with tenant_tx(m.tenant_id) as conn:
        return monitor.list_audit(conn, m.tenant_id, action, limit, cursor)


@router.get("/tenants/{tenant_id}/audit-events/verify", tags=["Audit"])
def verify_audit(m: ReadAudit) -> dict[str, Any]:
    with tenant_tx(m.tenant_id) as conn:
        return monitor.verify_audit(conn, m.tenant_id)


@router.get("/tenants/{tenant_id}/audit-events/export", tags=["Audit"])
def export_audit(m: ReadAudit) -> StreamingResponse:
    return StreamingResponse(
        monitor.export_audit_rows(m.tenant_id),
        media_type="text/csv; charset=utf-8",
        headers={
            "Content-Disposition": f'attachment; filename="ringsays-audit-{m.tenant_id}.csv"',
            "Cache-Control": "no-store",
        },
    )
