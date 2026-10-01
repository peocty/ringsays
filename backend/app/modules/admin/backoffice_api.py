"""Back office routes (internal deployment only; tenant facing deployment answers 404)."""

from __future__ import annotations

from typing import Annotated, Any, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Response
from pydantic import BaseModel, ConfigDict, EmailStr, StringConstraints

from app.core.db import backoffice_tx

from . import backoffice
from .access import Staff, staff
from .api import Localised, Now, ReasonIn

router = APIRouter(prefix="/admin/v1", tags=["Back office"])

Reviewer = Annotated[Staff, Depends(staff("RS_REVIEWER"))]
Operator = Annotated[Staff, Depends(staff("RS_ADMIN"))]
AnyStaff = Annotated[Staff, Depends(staff("RS_REVIEWER", "RS_ADMIN"))]


class TenantCreateIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    legal_name: Localised
    sector: Literal["BANK", "INSURANCE", "FINANCE", "GOVERNMENT"]
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
    admin_email: Annotated[EmailStr, StringConstraints(max_length=254)]
    admin_name: Annotated[str, StringConstraints(max_length=120)] | None = None


class DecisionIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    decision: Literal["APPROVE", "REJECT"]
    reason: Annotated[str, StringConstraints(min_length=3, max_length=500, strip_whitespace=True)]


@router.get("/backoffice/tenants")
def list_tenants(
    s: AnyStaff, status: Literal["PENDING", "VERIFIED", "SUSPENDED"] | None = None
) -> dict[str, Any]:
    with backoffice_tx() as conn:
        return {"items": backoffice.list_tenants(conn, status)}


@router.post("/backoffice/tenants", status_code=201)
def create_tenant(body: TenantCreateIn, s: Operator, now: Now) -> dict[str, Any]:
    with backoffice_tx() as conn:
        return backoffice.create_tenant(conn, s, body.model_dump(mode="json"), now)


@router.post("/backoffice/tenants/{tenant_id}/suspend")
def suspend_tenant(tenant_id: UUID, body: ReasonIn, s: Operator, now: Now) -> dict[str, Any]:
    with backoffice_tx() as conn:
        return backoffice.suspend(conn, s, tenant_id, body.reason, now)


@router.post("/backoffice/tenants/{tenant_id}/reinstate")
def reinstate_tenant(tenant_id: UUID, body: ReasonIn, s: Operator, now: Now) -> dict[str, Any]:
    with backoffice_tx() as conn:
        return backoffice.reinstate(conn, s, tenant_id, body.reason, now)


@router.get("/review/queue")
def review_queue(s: AnyStaff) -> dict[str, Any]:
    with backoffice_tx() as conn:
        return backoffice.queue(conn)


@router.get("/review/verifications/{request_id}")
def verification_detail(request_id: UUID, s: AnyStaff) -> dict[str, Any]:
    with backoffice_tx() as conn:
        return backoffice.verification_detail(conn, request_id)


@router.post("/review/verifications/{request_id}")
def decide_verification(request_id: UUID, body: DecisionIn, s: Reviewer, now: Now) -> dict[str, Any]:
    with backoffice_tx() as conn:
        return backoffice.decide_verification(conn, s, request_id, body.decision, body.reason, now)


@router.get("/review/documents/{document_id}/file")
def download_document(document_id: UUID, s: AnyStaff) -> Response:
    with backoffice_tx() as conn:
        data, content_type, name = backoffice.document_file(conn, document_id)
    return Response(
        data,
        media_type=content_type,
        headers={
            "Content-Disposition": f'attachment; filename="{name}"',
            "X-Content-Type-Options": "nosniff",
            "Cache-Control": "no-store",
            "Content-Security-Policy": "default-src 'none'; sandbox",
        },
    )


@router.post("/review/purpose-codes/{tenant_id}/{code}")
def decide_purpose_code(
    tenant_id: UUID, code: str, body: DecisionIn, s: Reviewer, now: Now
) -> dict[str, Any]:
    with backoffice_tx() as conn:
        return backoffice.decide_purpose_code(conn, s, tenant_id, code, body.decision, body.reason, now)


@router.post("/review/calling-numbers/{number_id}")
def decide_number(number_id: UUID, body: DecisionIn, s: Reviewer, now: Now) -> dict[str, Any]:
    with backoffice_tx() as conn:
        return backoffice.decide_number(conn, s, number_id, body.decision, body.reason, now)
