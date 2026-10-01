"""Who may do what in the portal and back office.

The identity provider authenticates; this module authorises. Memberships and roles are read from the
database on every request, so removing a role or disabling a person takes effect immediately.
A person who is not an active member of a tenant gets 403 for every path of that tenant, whether or not
the tenant exists, so tenant ids cannot be probed.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from typing import Annotated
from uuid import UUID

from fastapi import Depends, Header, Path
from sqlalchemy import select, update

from app.core import oidc
from app.core.config import settings
from app.core.db import backoffice_tx, tenant_tx
from app.core.tables import portal_users, staff_users, tenants
from app.modules.intent.errors import IntentError


class Unauthenticated(IntentError):
    code = "unauthenticated"
    http_status = 401


class BadRequest(IntentError):
    code = "validation_failed"
    http_status = 400


class Forbidden(IntentError):
    code = "forbidden"
    http_status = 403


class NotFound(IntentError):
    code = "not_found"
    http_status = 404


class Conflict(IntentError):
    code = "conflict"
    http_status = 409


class Perm(StrEnum):
    ORG_READ = "org.read"
    ORG_MANAGE = "org.manage"
    USERS_MANAGE = "users.manage"
    CATALOGUE_PROPOSE = "catalogue.propose"
    INTEGRATION_READ = "integration.read"
    INTEGRATION_MANAGE = "integration.manage"
    INTENTS_READ_ALL = "intents.read_all"
    INTENTS_READ_OWN = "intents.read_own"
    AUDIT_READ = "audit.read"


ROLE_PERMISSIONS: dict[str, frozenset[Perm]] = {
    "TENANT_ADMIN": frozenset(
        {
            Perm.ORG_READ,
            Perm.ORG_MANAGE,
            Perm.USERS_MANAGE,
            Perm.CATALOGUE_PROPOSE,
            Perm.INTEGRATION_READ,
            Perm.INTENTS_READ_ALL,
            Perm.AUDIT_READ,
        }
    ),
    # Separation of duties: only integration admins create or revoke credentials and webhooks.
    "INTEGRATION_ADMIN": frozenset({Perm.ORG_READ, Perm.INTEGRATION_READ, Perm.INTEGRATION_MANAGE}),
    "SUPERVISOR": frozenset({Perm.ORG_READ, Perm.INTENTS_READ_ALL}),
    "AGENT": frozenset({Perm.ORG_READ, Perm.INTENTS_READ_OWN}),
    "COMPLIANCE": frozenset({Perm.ORG_READ, Perm.CATALOGUE_PROPOSE, Perm.INTENTS_READ_ALL, Perm.AUDIT_READ}),
}
STAFF_ROLES = frozenset({"RS_REVIEWER", "RS_ADMIN"})


def permissions_for(roles: list[str] | frozenset[str]) -> frozenset[Perm]:
    out: set[Perm] = set()
    for r in roles:
        out |= ROLE_PERMISSIONS.get(r, frozenset())
    return frozenset(out)


@dataclass(frozen=True, slots=True)
class Member:
    user_id: UUID
    tenant_id: UUID
    email: str
    roles: frozenset[str]
    agent_id: str | None
    tenant_status: str

    @property
    def actor(self) -> str:
        return f"portal:{self.user_id}"

    @property
    def permissions(self) -> frozenset[Perm]:
        return permissions_for(self.roles)


@dataclass(frozen=True, slots=True)
class Staff:
    staff_id: UUID
    email: str
    roles: frozenset[str]

    @property
    def actor(self) -> str:
        return f"staff:{self.staff_id}"


def bearer(authorization: Annotated[str | None, Header()] = None) -> str:
    if not authorization or not authorization.lower().startswith("bearer "):
        raise Unauthenticated("Missing bearer token")
    return authorization.split(" ", 1)[1].strip()


def current_identity(token: Annotated[str, Depends(bearer)]) -> oidc.Identity:
    try:
        return oidc.verify(token)
    except oidc.InvalidIdentityToken as exc:
        raise Unauthenticated("Invalid or expired sign in; sign in again") from exc


IdentityDep = Annotated[oidc.Identity, Depends(current_identity)]


def load_member(identity: oidc.Identity, tenant_id: UUID) -> Member:
    if identity.issuer != settings.admin_oidc_issuer:
        raise Forbidden("Not a member of this organisation")
    with tenant_tx(tenant_id) as conn:
        row = conn.execute(
            select(portal_users).where(
                portal_users.c.oidc_issuer == identity.issuer,
                portal_users.c.oidc_subject == identity.subject,
                portal_users.c.status == "ACTIVE",
            )
        ).one_or_none()
        status = conn.execute(
            select(tenants.c.verification_status).where(tenants.c.id == tenant_id)
        ).scalar_one_or_none()
    if row is None or status is None:
        raise Forbidden("Not a member of this organisation")
    return Member(row.id, tenant_id, row.email, frozenset(row.roles), row.agent_id, status)


def member(identity: IdentityDep, tenant_id: Annotated[UUID, Path()]) -> Member:
    return load_member(identity, tenant_id)


def need(*perms: Perm, write: bool = False):  # type: ignore[no-untyped-def]
    """Dependency: member holding any of `perms`. Writes are refused while tenant is suspended."""

    def dep(m: Annotated[Member, Depends(member)]) -> Member:
        if not (m.permissions & set(perms)):
            raise Forbidden("Your role does not allow this")
        if write and m.tenant_status == "SUSPENDED":
            raise Forbidden("Organisation is suspended; changes are not allowed")
        return m

    return dep


# Staff


def load_staff(identity: oidc.Identity, now: datetime | None = None) -> Staff | None:
    """Active staff record for this identity, binding a seeded staff email on first verified sign in."""
    if not settings.backoffice_enabled or identity.issuer != settings.staff_oidc_issuer:
        return None
    now = now or datetime.now(UTC)
    with backoffice_tx() as conn:
        row = conn.execute(
            select(staff_users).where(
                staff_users.c.oidc_issuer == identity.issuer,
                staff_users.c.oidc_subject == identity.subject,
            )
        ).one_or_none()
        if row is None and identity.email_verified and identity.email:
            row = conn.execute(
                update(staff_users)
                .where(staff_users.c.email == identity.email, staff_users.c.oidc_subject.is_(None))
                .values(oidc_issuer=identity.issuer, oidc_subject=identity.subject, last_sign_in_at=now)
                .returning(*staff_users.c)
            ).one_or_none()
    if row is None or row.status != "ACTIVE":
        return None
    return Staff(row.id, row.email, frozenset(row.roles) & STAFF_ROLES)


def staff(*roles: str):  # type: ignore[no-untyped-def]
    """Dependency for back office paths. Tenant facing deployment answers 404."""

    def dep(identity: IdentityDep) -> Staff:
        if not settings.backoffice_enabled:
            raise NotFound("Not found")
        s = load_staff(identity)
        if s is None or not (s.roles & set(roles)):
            raise Forbidden("RingSays staff role required")
        return s

    return dep
