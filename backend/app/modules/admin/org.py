"""Organisation management for the portal: profile, departments, agents, calling numbers, verification
evidence, people and roles. Every function runs in a tenant scoped transaction and audits its writes."""

from __future__ import annotations

import hashlib
import re
from datetime import datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import Connection, func, insert, select, text, update
from sqlalchemy.exc import IntegrityError

from app.core.config import settings
from app.core.tables import (
    agents,
    calling_numbers,
    departments,
    portal_users,
    tenants,
    verification_documents,
    verification_requests,
)
from app.modules.audit import service as audit
from app.modules.intent.errors import RuleViolation
from app.platform import blobs

from .access import Conflict, Member, NotFound

MAX_DOCUMENT_BYTES = 5 * 1024 * 1024
MAX_DOCUMENTS = 20
REGULATED_SECTORS = {"BANK", "INSURANCE", "FINANCE"}


def iso(dt: datetime | None) -> str | None:
    return dt.isoformat() if dt else None


def mask_phone(phone: str) -> str:
    """+966500000001 -> +9665•••••001. Enough to recognise, not enough to call."""
    if len(phone) < 9:
        return phone[:2] + "•" * max(len(phone) - 2, 0)
    return phone[:5] + "•" * (len(phone) - 8) + phone[-3:]


def _audit(
    conn: Connection,
    m: Member,
    action: str,
    obj_type: str,
    obj_id: str,
    now: datetime,
    reason: str | None = None,
) -> None:
    audit.append(
        conn,
        tenant_id=m.tenant_id,
        actor=m.actor,
        action=action,
        object_type=obj_type,
        object_id=obj_id,
        at=now,
        reason=reason,
    )


# Tenant profile


def tenant_out(r: Any) -> dict[str, Any]:
    return {
        "tenant_id": str(r.id),
        "legal_name": {"en": r.legal_name_en, "ar": r.legal_name_ar},
        "commercial_registration": r.cr_number,
        "domain": r.domain,
        "sector": r.sector,
        "residency_region": r.residency_region,
        "verification_status": r.verification_status,
        "suspended_reason": r.suspended_reason,
        "created_at": iso(r.created_at),
    }


def get_tenant(conn: Connection, tenant_id: UUID) -> dict[str, Any]:
    row = conn.execute(select(tenants).where(tenants.c.id == tenant_id)).one_or_none()
    if row is None:
        raise NotFound("Organisation not found")
    return tenant_out(row)


def _open_request(conn: Connection) -> Any:
    return conn.execute(
        select(verification_requests).where(verification_requests.c.status == "SUBMITTED")
    ).one_or_none()


def lock_pending_tenant(conn: Connection, tenant_id: UUID, what: str) -> Any:
    """Lock the tenant row, then check it is pending and nothing is under review.

    Submit and RingSays' decision lock the same row, so the check and the write that follows cannot
    interleave with them. A database guard enforces the same rule (migration 0004)."""
    t = conn.execute(select(tenants).where(tenants.c.id == tenant_id).with_for_update()).one()
    if t.verification_status != "PENDING":
        raise Conflict(f"{what} is locked after verification; contact RingSays to change it")
    if _open_request(conn) is not None:
        raise Conflict(f"{what} is locked while verification is under review")
    return t


def update_tenant(conn: Connection, m: Member, body: dict[str, Any], now: datetime) -> dict[str, Any]:
    lock_pending_tenant(conn, m.tenant_id, "Profile")
    values: dict[str, Any] = {}
    if "legal_name" in body:
        values["legal_name_en"] = body["legal_name"]["en"]
        values["legal_name_ar"] = body["legal_name"]["ar"]
    if "commercial_registration" in body:
        values["cr_number"] = body["commercial_registration"]
    if "domain" in body:
        values["domain"] = body["domain"].lower()
    conn.execute(update(tenants).where(tenants.c.id == m.tenant_id).values(**values))
    _audit(conn, m, "tenant.update", "tenant", str(m.tenant_id), now)
    return get_tenant(conn, m.tenant_id)


# Departments


def _dept_out(r: Any) -> dict[str, Any]:
    return {"department_id": str(r.id), "name": {"en": r.name_en, "ar": r.name_ar}}


def list_departments(conn: Connection) -> list[dict[str, Any]]:
    rows = conn.execute(select(departments).order_by(departments.c.name_en)).all()
    return [_dept_out(r) for r in rows]


def create_department(conn: Connection, m: Member, name: dict[str, str], now: datetime) -> dict[str, Any]:
    dept_id = uuid4()
    conn.execute(
        insert(departments).values(id=dept_id, tenant_id=m.tenant_id, name_en=name["en"], name_ar=name["ar"])
    )
    _audit(conn, m, "department.create", "department", str(dept_id), now)
    return {"department_id": str(dept_id), "name": name}


def update_department(
    conn: Connection, m: Member, dept_id: UUID, name: dict[str, str], now: datetime
) -> dict[str, Any]:
    row = conn.execute(
        update(departments)
        .where(departments.c.id == dept_id)
        .values(name_en=name["en"], name_ar=name["ar"])
        .returning(*departments.c)
    ).one_or_none()
    if row is None:
        raise NotFound("Department not found")
    _audit(conn, m, "department.update", "department", str(dept_id), now)
    return _dept_out(row)


def _require_department(conn: Connection, dept_id: UUID) -> None:
    if conn.execute(select(departments.c.id).where(departments.c.id == dept_id)).first() is None:
        raise RuleViolation("department_id does not refer to a department of this organisation")


# Agents


def _agent_out(r: Any) -> dict[str, Any]:
    return {
        "agent_id": r.agent_id,
        "display_name": {"en": r.display_name_en, "ar": r.display_name_ar},
        "department_id": str(r.department_id),
        "employee_ref": r.employee_ref,
        "active": r.active,
    }


def list_agents(conn: Connection, limit: int, cursor: str | None) -> dict[str, Any]:
    q = select(agents).order_by(agents.c.agent_id).limit(limit + 1)
    if cursor:
        q = q.where(agents.c.agent_id > cursor)
    rows = conn.execute(q).all()
    items = [_agent_out(r) for r in rows[:limit]]
    return {"items": items, "next_cursor": items[-1]["agent_id"] if len(rows) > limit else None}


def create_agent(conn: Connection, m: Member, body: dict[str, Any], now: datetime) -> dict[str, Any]:
    _require_department(conn, body["department_id"])
    exists = conn.execute(select(agents.c.agent_id).where(agents.c.agent_id == body["agent_id"])).first()
    if exists:
        raise Conflict(f"Agent {body['agent_id']} already exists")
    try:
        with conn.begin_nested():
            row = _insert_agent(conn, m, body)
    except IntegrityError as exc:
        raise Conflict(f"Agent {body['agent_id']} already exists") from exc
    _audit(conn, m, "agent.create", "agent", body["agent_id"], now)
    return _agent_out(row)


def _insert_agent(conn: Connection, m: Member, body: dict[str, Any]) -> Any:
    return conn.execute(
        insert(agents)
        .values(
            tenant_id=m.tenant_id,
            agent_id=body["agent_id"],
            department_id=body["department_id"],
            display_name_en=body["display_name"]["en"],
            display_name_ar=body["display_name"]["ar"],
            employee_ref=body.get("employee_ref"),
            active=True,
        )
        .returning(*agents.c)
    ).one()


def update_agent(
    conn: Connection, m: Member, agent_id: str, body: dict[str, Any], now: datetime
) -> dict[str, Any]:
    values: dict[str, Any] = {}
    if "department_id" in body:
        _require_department(conn, body["department_id"])
        values["department_id"] = body["department_id"]
    if "display_name" in body:
        values["display_name_en"] = body["display_name"]["en"]
        values["display_name_ar"] = body["display_name"]["ar"]
    if "employee_ref" in body:
        values["employee_ref"] = body["employee_ref"]
    if "active" in body:
        values["active"] = body["active"]
    row = conn.execute(
        update(agents).where(agents.c.agent_id == agent_id).values(**values).returning(*agents.c)
    ).one_or_none()
    if row is None:
        raise NotFound("Agent not found")
    action = "agent.deactivate" if body.get("active") is False else "agent.update"
    _audit(conn, m, action, "agent", agent_id, now)
    return _agent_out(row)


# Calling numbers


def number_out(r: Any) -> dict[str, Any]:
    return {
        "number_id": str(r.id),
        "phone_masked": mask_phone(r.phone),
        "department_id": str(r.department_id) if r.department_id else None,
        "status": r.status,
        "cst_entity_name_registered": r.cst_registered,
        "review_reason": r.review_reason,
        "created_at": iso(r.created_at),
    }


def list_numbers(conn: Connection) -> list[dict[str, Any]]:
    rows = conn.execute(select(calling_numbers).order_by(calling_numbers.c.created_at)).all()
    return [number_out(r) for r in rows]


def add_number(conn: Connection, m: Member, body: dict[str, Any], now: datetime) -> dict[str, Any]:
    if body.get("department_id"):
        _require_department(conn, body["department_id"])
    existing = conn.execute(
        select(calling_numbers).where(calling_numbers.c.phone == body["phone"])
    ).one_or_none()
    if existing is not None and existing.status != "REVOKED":
        raise Conflict("This number is already registered for your organisation")
    if existing is not None:
        # Revoked rows are kept as history, so re-registering goes through RingSays.
        raise Conflict("This number was revoked; contact RingSays to register it again")
    number_id = uuid4()
    row = conn.execute(
        insert(calling_numbers)
        .values(
            id=number_id,
            tenant_id=m.tenant_id,
            department_id=body.get("department_id"),
            phone=body["phone"],
            status="PENDING_VERIFICATION",
            cst_registered=body["cst_entity_name_registered"],
            created_at=now,
        )
        .returning(*calling_numbers.c)
    ).one()
    _audit(conn, m, "calling_number.add", "calling_number", str(number_id), now)
    return number_out(row)


def revoke_number(conn: Connection, m: Member, number_id: UUID, reason: str, now: datetime) -> dict[str, Any]:
    row = conn.execute(
        update(calling_numbers)
        .where(calling_numbers.c.id == number_id)
        .values(status="REVOKED")
        .returning(*calling_numbers.c)
    ).one_or_none()
    if row is None:
        raise NotFound("Calling number not found")
    _audit(conn, m, "calling_number.revoke", "calling_number", str(number_id), now, reason)
    return number_out(row)


# Verification evidence


def required_kinds(sector: str) -> list[str]:
    kinds = ["COMMERCIAL_REGISTRATION", "AUTHORISATION_LETTER"]
    if sector in REGULATED_SECTORS:
        kinds.insert(1, "REGULATOR_LICENCE")
    return kinds


def doc_out(r: Any) -> dict[str, Any]:
    return {
        "document_id": str(r.id),
        "kind": r.kind,
        "reference": r.reference,
        "file_name": r.file_name,
        "content_type": r.content_type,
        "size_bytes": r.size_bytes,
        "sha256": r.sha256,
        "uploaded_at": iso(r.uploaded_at),
    }


def request_out(r: Any) -> dict[str, Any]:
    return {
        "request_id": str(r.id),
        "status": r.status,
        "submitted_at": iso(r.submitted_at),
        "decided_at": iso(r.decided_at),
        "decision_reason": r.decision_reason,
    }


def get_verification(conn: Connection, tenant_id: UUID) -> dict[str, Any]:
    t = conn.execute(select(tenants).where(tenants.c.id == tenant_id)).one()
    docs = conn.execute(select(verification_documents).order_by(verification_documents.c.uploaded_at)).all()
    latest = conn.execute(
        select(verification_requests).order_by(verification_requests.c.submitted_at.desc()).limit(1)
    ).one_or_none()
    return {
        "tenant_status": t.verification_status,
        "documents": [doc_out(d) for d in docs],
        "required_kinds": required_kinds(t.sector),
        "latest_request": request_out(latest) if latest else None,
    }


_SAFE_NAME = re.compile(r"[^A-Za-z0-9._ -]+")


def safe_file_name(name: str | None) -> str:
    base = (name or "document").replace("\\", "/").rsplit("/", 1)[-1]
    cleaned = _SAFE_NAME.sub("_", base).strip(" .") or "document"
    return cleaned[:200]


def validate_document(data: bytes) -> str:
    """Content type from the bytes (never the client's claim). Called before anything is stored."""
    if not data or len(data) > MAX_DOCUMENT_BYTES:
        raise RuleViolation("File must be between 1 byte and 5 MB")
    content_type = blobs.sniff_content_type(data)
    if content_type is None:
        raise RuleViolation("File must be a PDF, PNG or JPEG")
    return content_type


def record_document(
    conn: Connection,
    m: Member,
    kind: str,
    reference: str | None,
    file_name: str | None,
    data: bytes,
    content_type: str,
    blob_key: str,
    now: datetime,
) -> dict[str, Any]:
    """Row for a blob already stored. Caller deletes the blob if this transaction does not commit."""
    lock_pending_tenant(conn, m.tenant_id, "Evidence")
    count = conn.execute(select(func.count()).select_from(verification_documents)).scalar_one()
    if count >= MAX_DOCUMENTS:
        raise Conflict(f"At most {MAX_DOCUMENTS} documents; remove one first")
    doc_id = uuid4()
    row = conn.execute(
        insert(verification_documents)
        .values(
            id=doc_id,
            tenant_id=m.tenant_id,
            kind=kind,
            reference=reference or None,
            file_name=safe_file_name(file_name),
            content_type=content_type,
            size_bytes=len(data),
            sha256=hashlib.sha256(data).hexdigest(),
            blob_key=blob_key,
            uploaded_by=m.actor,
            uploaded_at=now,
        )
        .returning(*verification_documents.c)
    ).one()
    _audit(conn, m, "verification.document_upload", "verification_document", str(doc_id), now)
    return doc_out(row)


def delete_document(conn: Connection, m: Member, doc_id: UUID, now: datetime) -> str:
    """Returns blob key; caller deletes blob after commit."""
    lock_pending_tenant(conn, m.tenant_id, "Evidence")
    row = conn.execute(
        text("DELETE FROM enterprise.verification_documents WHERE id = :i RETURNING blob_key"), {"i": doc_id}
    ).one_or_none()
    if row is None:
        raise NotFound("Document not found")
    _audit(conn, m, "verification.document_delete", "verification_document", str(doc_id), now)
    return str(row.blob_key)


def submit_verification(conn: Connection, m: Member, now: datetime) -> dict[str, Any]:
    t = conn.execute(select(tenants).where(tenants.c.id == m.tenant_id).with_for_update()).one()
    if t.verification_status != "PENDING":
        raise Conflict("Organisation is not waiting for verification")
    if _open_request(conn) is not None:
        raise Conflict("Verification is already under review")
    missing_profile = [
        f for f, v in (("commercial_registration", t.cr_number), ("domain", t.domain)) if not v
    ]
    if missing_profile:
        raise RuleViolation(f"Complete profile first: {', '.join(missing_profile)}")
    docs = conn.execute(select(verification_documents.c.id, verification_documents.c.kind)).all()
    missing = [k for k in required_kinds(t.sector) if k not in {d.kind for d in docs}]
    if missing:
        raise RuleViolation(f"Missing evidence: {', '.join(missing)}")
    req_id = uuid4()
    try:
        row = conn.execute(
            insert(verification_requests)
            .values(
                id=req_id,
                tenant_id=m.tenant_id,
                status="SUBMITTED",
                document_ids=[d.id for d in docs],
                submitted_by=m.actor,
                submitted_at=now,
            )
            .returning(*verification_requests.c)
        ).one()
    except IntegrityError as exc:
        raise Conflict("Verification is already under review") from exc
    _audit(conn, m, "verification.submit", "verification_request", str(req_id), now)
    return request_out(row)


# People and roles


def user_out(r: Any) -> dict[str, Any]:
    return {
        "user_id": str(r.id),
        "email": r.email,
        "display_name": r.display_name,
        "roles": sorted(r.roles),
        "agent_id": r.agent_id,
        "status": r.status,
        "created_at": iso(r.created_at),
        "last_sign_in_at": iso(r.last_sign_in_at),
    }


def list_users(conn: Connection) -> list[dict[str, Any]]:
    rows = conn.execute(select(portal_users).order_by(portal_users.c.email)).all()
    return [user_out(r) for r in rows]


def _check_agent_link(conn: Connection, roles: list[str], agent_id: str | None) -> None:
    if "AGENT" in roles and not agent_id:
        raise RuleViolation("AGENT role needs agent_id")
    if (
        agent_id
        and conn.execute(select(agents.c.agent_id).where(agents.c.agent_id == agent_id)).first() is None
    ):
        raise RuleViolation("agent_id does not refer to an agent of this organisation")


def invite_user(conn: Connection, m: Member, body: dict[str, Any], now: datetime) -> dict[str, Any]:
    """Invite by email. Inviting again an email whose invitation was never accepted renews it."""
    email = body["email"].strip().lower()
    roles = sorted(set(body["roles"]))
    _check_agent_link(conn, roles, body.get("agent_id"))
    conn.execute(text("SELECT pg_advisory_xact_lock(hashtext(:k))"), {"k": f"portal_users:{m.tenant_id}"})
    existing = conn.execute(select(portal_users).where(portal_users.c.email == email)).one_or_none()
    expires = now + timedelta(days=settings.invite_ttl_days)
    values = {
        "display_name": body.get("display_name"),
        "roles": roles,
        "agent_id": body.get("agent_id"),
        "status": "INVITED",
        "invited_by": m.actor,
        "invite_expires_at": expires,
    }
    if existing is not None:
        if existing.oidc_subject is not None or existing.status != "INVITED":
            raise Conflict("This email is already a member")
        row = conn.execute(
            update(portal_users)
            .where(portal_users.c.id == existing.id)
            .values(**values)
            .returning(*portal_users.c)
        ).one()
        action = "portal_user.reinvite"
    else:
        try:
            with conn.begin_nested():
                row = conn.execute(
                    insert(portal_users)
                    .values(id=uuid4(), tenant_id=m.tenant_id, email=email, created_at=now, **values)
                    .returning(*portal_users.c)
                ).one()
        except IntegrityError as exc:
            raise Conflict("This email is already a member or invited") from exc
        action = "portal_user.invite"
    _audit(conn, m, action, "portal_user", str(row.id), now, reason=",".join(roles))
    return user_out(row)


def update_user(
    conn: Connection, m: Member, user_id: UUID, body: dict[str, Any], now: datetime
) -> dict[str, Any]:
    # Serialise admin changes per tenant so two admins cannot each remove the other's admin role.
    conn.execute(text("SELECT pg_advisory_xact_lock(hashtext(:k))"), {"k": f"portal_users:{m.tenant_id}"})
    row = conn.execute(select(portal_users).where(portal_users.c.id == user_id)).one_or_none()
    if row is None:
        raise NotFound("Person not found")
    roles = sorted(set(body.get("roles", row.roles)))
    status = body.get("status", row.status)
    agent_id = body["agent_id"] if "agent_id" in body else row.agent_id
    if user_id == m.user_id and (status == "DISABLED" or roles != sorted(row.roles)):
        # Another administrator must change your roles, so no one can grant themselves more access.
        raise Conflict("You cannot disable yourself or change your own roles")
    if status == "ACTIVE" and row.oidc_subject is None:
        raise Conflict("This person has not accepted the invitation yet")
    _check_agent_link(conn, roles, agent_id)
    was_admin = row.status == "ACTIVE" and "TENANT_ADMIN" in row.roles
    stays_admin = status == "ACTIVE" and "TENANT_ADMIN" in roles
    if was_admin and not stays_admin:
        others = conn.execute(
            select(func.count())
            .select_from(portal_users)
            .where(
                portal_users.c.id != user_id,
                portal_users.c.status == "ACTIVE",
                portal_users.c.roles.any("TENANT_ADMIN"),
            )
        ).scalar_one()
        if others == 0:
            raise Conflict("At least one active administrator is required")
    new_status = row.status if row.status == "INVITED" and status != "DISABLED" else status
    updated = conn.execute(
        update(portal_users)
        .where(portal_users.c.id == user_id)
        .values(roles=roles, status=new_status, agent_id=agent_id)
        .returning(*portal_users.c)
    ).one()
    _audit(
        conn,
        m,
        "portal_user.update",
        "portal_user",
        str(user_id),
        now,
        reason=f"roles={','.join(roles)};status={new_status}",
    )
    return user_out(updated)
