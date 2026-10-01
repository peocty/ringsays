"""Enterprise module: tenant status, agents, calling numbers, purpose code catalogue, verification level.

All functions take a tenant scoped connection; row level security limits every query to that tenant.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from uuid import UUID

from sqlalchemy import Connection, insert, select

from app.core.tables import agents, calling_numbers, purpose_codes, tenants
from app.modules.intent.domain import Channel, Priority, VerificationLevel
from app.modules.intent.errors import IntentError, RuleViolation
from app.modules.intent.rules import PurposeCodePolicy


class TenantNotActive(IntentError):
    code = "tenant_not_active"
    http_status = 403


class UnknownAgent(IntentError):
    code = "unknown_agent"
    http_status = 422


class UnknownPurposeCode(IntentError):
    code = "unknown_purpose_code"
    http_status = 422


class PurposeCodeExists(IntentError):
    code = "purpose_code_exists"
    http_status = 409


@dataclass(frozen=True, slots=True)
class AgentInfo:
    agent_id: str
    department_id: UUID
    active: bool


def tenant_status(conn: Connection, tenant_id: UUID) -> str | None:
    return conn.execute(
        select(tenants.c.verification_status).where(tenants.c.id == tenant_id)
    ).scalar_one_or_none()


def require_active_tenant(conn: Connection, tenant_id: UUID) -> str:
    status = tenant_status(conn, tenant_id)
    if status is None or status == "SUSPENDED":
        raise TenantNotActive("Tenant is not active")
    return status


def get_agent(conn: Connection, agent_id: str) -> AgentInfo:
    row = conn.execute(select(agents).where(agents.c.agent_id == agent_id)).one_or_none()
    if row is None or not row.active:
        raise UnknownAgent(f"Agent {agent_id} is not registered or not active")
    return AgentInfo(agent_id=row.agent_id, department_id=row.department_id, active=row.active)


def purpose_policy(conn: Connection, code: str) -> PurposeCodePolicy:
    row = conn.execute(select(purpose_codes).where(purpose_codes.c.code == code)).one_or_none()
    if row is None:
        raise UnknownPurposeCode(f"Purpose code {code} is not in tenant catalogue")
    return PurposeCodePolicy(
        code=row.code,
        approved=row.status == "APPROVED",
        max_priority=Priority(row.max_priority),
        max_duration_min=row.max_duration_min,
        allowed_channels=frozenset(Channel(c) for c in row.allowed_channels),
    )


def verification_level(conn: Connection, tenant_id: UUID, agent: AgentInfo) -> VerificationLevel:
    """Computed, never supplied by caller.

    ORG_AGENT_NUMBER: tenant verified, agent active, and agent's department has a verified calling number.
    ORG: tenant verified. Tenants still PENDING verification cannot send intents at all, so a customer
    never receives an enterprise intent from an organisation RingSays has not verified.
    """
    status = require_active_tenant(conn, tenant_id)
    if status != "VERIFIED":
        raise TenantNotActive("Tenant verification is pending; intents cannot be sent yet")
    has_number = conn.execute(
        select(calling_numbers.c.id)
        .where(
            calling_numbers.c.status == "VERIFIED",
            (calling_numbers.c.department_id == agent.department_id)
            | calling_numbers.c.department_id.is_(None),
        )
        .limit(1)
    ).first()
    return VerificationLevel.ORG_AGENT_NUMBER if has_number else VerificationLevel.ORG


def list_purpose_codes(conn: Connection, limit: int, after: str | None) -> list[dict[str, Any]]:
    q = select(purpose_codes).order_by(purpose_codes.c.code).limit(limit)
    if after:
        q = q.where(purpose_codes.c.code > after)
    return [_code_out(r) for r in conn.execute(q).all()]


def propose_purpose_code(conn: Connection, tenant_id: UUID, body: dict[str, Any]) -> dict[str, Any]:
    if Priority(body["max_priority"]) is Priority.URGENT and not body["code"].startswith(
        ("CARD.", "FRAUD.", "SECURITY.")
    ):
        raise RuleViolation("URGENT is reserved for card, fraud and security purpose codes")
    exists = conn.execute(select(purpose_codes.c.code).where(purpose_codes.c.code == body["code"])).first()
    if exists:
        raise PurposeCodeExists(f"Purpose code {body['code']} already exists")
    conn.execute(
        insert(purpose_codes).values(
            tenant_id=tenant_id,
            code=body["code"],
            version=1,
            display_en=body["display_text"]["en"],
            display_ar=body["display_text"]["ar"],
            max_priority=body["max_priority"],
            max_duration_min=body["max_duration_min"],
            allowed_channels=list(body["allowed_channels"]),
            status="PENDING_REVIEW",
        )
    )
    row = conn.execute(select(purpose_codes).where(purpose_codes.c.code == body["code"])).one()
    return _code_out(row)


def _code_out(r: Any) -> dict[str, Any]:
    return {
        "code": r.code,
        "display_text": {"en": r.display_en, "ar": r.display_ar},
        "max_priority": r.max_priority,
        "max_duration_min": r.max_duration_min,
        "allowed_channels": list(r.allowed_channels),
        "status": r.status,
        "version": r.version,
    }
