"""RingSays back office: onboarding organisations and deciding reviews.

Runs on the back office database role (see migration 0004), which may read organisation, catalogue and
verification rows of every tenant but never intents or identity. Each decision is written to the
affected tenant's audit chain, so the tenant's compliance team sees who decided what and why.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import Connection, insert, select, update
from sqlalchemy.exc import IntegrityError

from app.core import auth
from app.core.config import settings
from app.core.tables import (
    calling_numbers,
    portal_users,
    purpose_codes,
    tenants,
    verification_documents,
    verification_requests,
)
from app.modules.audit import service as audit
from app.platform import blobs

from .access import Conflict, NotFound, Staff
from .integration import code_out
from .org import doc_out, number_out, request_out, tenant_out


def _audit(
    conn: Connection,
    s: Staff,
    tenant_id: UUID,
    action: str,
    obj_type: str,
    obj_id: str,
    now: datetime,
    reason: str | None = None,
) -> None:
    audit.append(
        conn,
        tenant_id=tenant_id,
        actor=s.actor,
        action=action,
        object_type=obj_type,
        object_id=obj_id,
        at=now,
        reason=reason,
    )


def _name(r: Any) -> dict[str, str]:
    return {"en": r.legal_name_en, "ar": r.legal_name_ar}


def list_tenants(conn: Connection, status: str | None) -> list[dict[str, Any]]:
    q = select(tenants).where(tenants.c.residency_region == settings.residency_region)
    if status:
        q = q.where(tenants.c.verification_status == status)
    return [tenant_out(r) for r in conn.execute(q.order_by(tenants.c.created_at.desc())).all()]


def create_tenant(conn: Connection, s: Staff, body: dict[str, Any], now: datetime) -> dict[str, Any]:
    tenant_id = uuid4()
    conn.execute(
        insert(tenants).values(
            id=tenant_id,
            legal_name_en=body["legal_name"]["en"],
            legal_name_ar=body["legal_name"]["ar"],
            domain=(body.get("domain") or "").lower() or None,
            sector=body["sector"],
            residency_region=settings.residency_region,
            verification_status="PENDING",
            created_at=now,
        )
    )
    admin_id = uuid4()
    conn.execute(
        insert(portal_users).values(
            id=admin_id,
            tenant_id=tenant_id,
            email=body["admin_email"].strip().lower(),
            display_name=body.get("admin_name"),
            roles=["TENANT_ADMIN"],
            status="INVITED",
            invited_by=s.actor,
            invite_expires_at=now + timedelta(days=settings.invite_ttl_days),
            created_at=now,
        )
    )
    _audit(conn, s, tenant_id, "tenant.create", "tenant", str(tenant_id), now)
    _audit(conn, s, tenant_id, "portal_user.invite", "portal_user", str(admin_id), now, reason="TENANT_ADMIN")
    return tenant_out(conn.execute(select(tenants).where(tenants.c.id == tenant_id)).one())


def _tenant_for_update(conn: Connection, tenant_id: UUID) -> Any:
    row = conn.execute(
        select(tenants)
        .where(tenants.c.id == tenant_id, tenants.c.residency_region == settings.residency_region)
        .with_for_update()
    ).one_or_none()
    if row is None:
        raise NotFound("Organisation not found")
    return row


def suspend(conn: Connection, s: Staff, tenant_id: UUID, reason: str, now: datetime) -> dict[str, Any]:
    row = _tenant_for_update(conn, tenant_id)
    if row.verification_status == "SUSPENDED":
        raise Conflict("Organisation is already suspended")
    conn.execute(
        update(tenants)
        .where(tenants.c.id == tenant_id)
        .values(
            verification_status="SUSPENDED",
            suspended_reason=reason,
            status_before_suspension=row.verification_status,
        )
    )
    _audit(conn, s, tenant_id, "tenant.suspend", "tenant", str(tenant_id), now, reason)
    auth.clear_client_status_cache()
    return tenant_out(conn.execute(select(tenants).where(tenants.c.id == tenant_id)).one())


def reinstate(conn: Connection, s: Staff, tenant_id: UUID, reason: str, now: datetime) -> dict[str, Any]:
    row = _tenant_for_update(conn, tenant_id)
    if row.verification_status != "SUSPENDED":
        raise Conflict("Organisation is not suspended")
    approved = conn.execute(
        select(verification_requests.c.id).where(
            verification_requests.c.tenant_id == tenant_id, verification_requests.c.status == "APPROVED"
        )
    ).first()
    status = "VERIFIED" if approved or row.status_before_suspension == "VERIFIED" else "PENDING"
    conn.execute(
        update(tenants)
        .where(tenants.c.id == tenant_id)
        .values(verification_status=status, suspended_reason=None, status_before_suspension=None)
    )
    _audit(conn, s, tenant_id, "tenant.reinstate", "tenant", str(tenant_id), now, reason)
    auth.clear_client_status_cache()
    return tenant_out(conn.execute(select(tenants).where(tenants.c.id == tenant_id)).one())


# Review queue


def queue(conn: Connection) -> dict[str, Any]:
    region = tenants.c.residency_region == settings.residency_region
    ver = conn.execute(
        select(verification_requests, tenants.c.legal_name_en, tenants.c.legal_name_ar, tenants.c.sector)
        .join(tenants, tenants.c.id == verification_requests.c.tenant_id)
        .where(verification_requests.c.status == "SUBMITTED", region)
        .order_by(verification_requests.c.submitted_at)
        .limit(200)
    ).all()
    codes = conn.execute(
        select(purpose_codes, tenants.c.legal_name_en, tenants.c.legal_name_ar)
        .join(tenants, tenants.c.id == purpose_codes.c.tenant_id)
        .where(purpose_codes.c.status == "PENDING_REVIEW", region)
        .order_by(purpose_codes.c.proposed_at)
        .limit(200)
    ).all()
    numbers = conn.execute(
        select(calling_numbers, tenants.c.legal_name_en, tenants.c.legal_name_ar)
        .join(tenants, tenants.c.id == calling_numbers.c.tenant_id)
        .where(calling_numbers.c.status == "PENDING_VERIFICATION", region)
        .order_by(calling_numbers.c.created_at)
        .limit(200)
    ).all()
    return {
        "verifications": [
            {
                "request_id": str(r.id),
                "tenant_id": str(r.tenant_id),
                "legal_name": _name(r),
                "sector": r.sector,
                "submitted_at": r.submitted_at.isoformat(),
            }
            for r in ver
        ],
        "purpose_codes": [
            {**code_out(r), "tenant_id": str(r.tenant_id), "tenant_name": _name(r)} for r in codes
        ],
        "calling_numbers": [
            {**number_out(r), "tenant_id": str(r.tenant_id), "tenant_name": _name(r), "phone": r.phone}
            for r in numbers
        ],
    }


def _request(conn: Connection, request_id: UUID, *, for_update: bool = False) -> Any:
    q = (
        select(verification_requests)
        .join(tenants, tenants.c.id == verification_requests.c.tenant_id)
        .where(
            verification_requests.c.id == request_id, tenants.c.residency_region == settings.residency_region
        )
    )
    row = conn.execute(q.with_for_update(of=verification_requests) if for_update else q).one_or_none()
    if row is None:
        raise NotFound("Verification request not found")
    return row


def verification_detail(conn: Connection, request_id: UUID) -> dict[str, Any]:
    req = _request(conn, request_id)
    t = conn.execute(select(tenants).where(tenants.c.id == req.tenant_id)).one()
    docs = conn.execute(
        select(verification_documents)
        .where(verification_documents.c.id.in_(req.document_ids))
        .order_by(verification_documents.c.uploaded_at)
    ).all()
    return {"request": request_out(req), "tenant": tenant_out(t), "documents": [doc_out(d) for d in docs]}


def decide_verification(
    conn: Connection, s: Staff, request_id: UUID, decision: str, reason: str, now: datetime
) -> dict[str, Any]:
    req = _request(conn, request_id, for_update=True)
    if req.status != "SUBMITTED":
        raise Conflict("This request has already been decided")
    tenant = _tenant_for_update(conn, req.tenant_id)
    status = "APPROVED" if decision == "APPROVE" else "REJECTED"
    row = conn.execute(
        update(verification_requests)
        .where(verification_requests.c.id == request_id)
        .values(status=status, decided_by=s.actor, decided_at=now, decision_reason=reason)
        .returning(*verification_requests.c)
    ).one()
    if status == "APPROVED" and tenant.verification_status == "PENDING":
        conn.execute(
            update(tenants).where(tenants.c.id == req.tenant_id).values(verification_status="VERIFIED")
        )
    _audit(
        conn,
        s,
        req.tenant_id,
        f"verification.{status.lower()}",
        "verification_request",
        str(request_id),
        now,
        reason,
    )
    return request_out(row)


def document_file(conn: Connection, document_id: UUID) -> tuple[bytes, str, str]:
    row = conn.execute(
        select(verification_documents)
        .join(tenants, tenants.c.id == verification_documents.c.tenant_id)
        .where(
            verification_documents.c.id == document_id,
            tenants.c.residency_region == settings.residency_region,
        )
    ).one_or_none()
    if row is None:
        raise NotFound("Document not found")
    return blobs.get_store().get(row.blob_key), row.content_type, row.file_name


def decide_purpose_code(
    conn: Connection, s: Staff, tenant_id: UUID, code: str, decision: str, reason: str, now: datetime
) -> dict[str, Any]:
    _tenant_for_update(conn, tenant_id)
    row = conn.execute(
        select(purpose_codes)
        .where(purpose_codes.c.tenant_id == tenant_id, purpose_codes.c.code == code)
        .with_for_update()
    ).one_or_none()
    if row is None:
        raise NotFound("Purpose code not found")
    if row.status != "PENDING_REVIEW":
        raise Conflict("This purpose code has already been decided")
    status = "APPROVED" if decision == "APPROVE" else "REJECTED"
    updated = conn.execute(
        update(purpose_codes)
        .where(purpose_codes.c.tenant_id == tenant_id, purpose_codes.c.code == code)
        .values(status=status, reviewed_at=now, reviewed_by=s.actor, review_reason=reason)
        .returning(*purpose_codes.c)
    ).one()
    _audit(conn, s, tenant_id, f"purpose_code.{status.lower()}", "purpose_code", code, now, reason)
    return code_out(updated)


def decide_number(
    conn: Connection, s: Staff, number_id: UUID, decision: str, reason: str, now: datetime
) -> dict[str, Any]:
    row = conn.execute(
        select(calling_numbers)
        .join(tenants, tenants.c.id == calling_numbers.c.tenant_id)
        .where(calling_numbers.c.id == number_id, tenants.c.residency_region == settings.residency_region)
        .with_for_update(of=calling_numbers)
    ).one_or_none()
    if row is None:
        raise NotFound("Calling number not found")
    if row.status != "PENDING_VERIFICATION":
        raise Conflict("This number has already been decided")
    status = "VERIFIED" if decision == "APPROVE" else "REVOKED"
    try:
        with conn.begin_nested():
            updated = conn.execute(
                update(calling_numbers)
                .where(calling_numbers.c.id == number_id)
                .values(status=status, reviewed_at=now, reviewed_by=s.actor, review_reason=reason)
                .returning(*calling_numbers.c)
            ).one()
    except IntegrityError as exc:
        raise Conflict("This number is already verified for another organisation") from exc
    _audit(
        conn,
        s,
        row.tenant_id,
        f"calling_number.{status.lower()}",
        "calling_number",
        str(number_id),
        now,
        reason,
    )
    return number_out(updated)
