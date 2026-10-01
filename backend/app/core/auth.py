"""Enterprise authentication: OAuth 2.0 client credentials and scoped bearer tokens.

Local environment signs HS256 tokens with a configured secret. Production must replace `TokenSigner`
with an asymmetric key held in KMS; settings refuse the local secret outside local environment.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Annotated
from uuid import UUID

import jwt
from fastapi import APIRouter, Depends, Form, Header, HTTPException
from fastapi.responses import JSONResponse
from sqlalchemy import text

from app.core.config import settings
from app.core.db import anonymous_tx, tenant_tx
from app.core.ids import uuid7
from app.core.secrets import generate_secret, hash_secret, verify_secret

ALGORITHM = "HS256"
_DUMMY_HASH = hash_secret(generate_secret())
# Revoked clients and suspended tenants lose access within this many seconds, not at token expiry.
CLIENT_STATUS_TTL_S = 30.0
_status_cache: dict[str, tuple[float, bool]] = {}
AUDIENCE = "ringsays-enterprise-api"

router = APIRouter(tags=["Auth"])


@dataclass(frozen=True, slots=True)
class Principal:
    tenant_id: UUID
    client_id: str
    scopes: frozenset[str]

    @property
    def actor(self) -> str:
        return f"client:{self.client_id}"


class AuthError(HTTPException):
    def __init__(self, status: int, detail: str) -> None:
        super().__init__(status_code=status, detail=detail, headers={"WWW-Authenticate": "Bearer"})


def issue_token(tenant_id: UUID, client_id: str, scopes: frozenset[str], now: datetime) -> str:
    claims = {
        "iss": settings.jwt_issuer,
        "aud": AUDIENCE,
        "sub": client_id,
        "tid": str(tenant_id),
        "scope": " ".join(sorted(scopes)),
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(seconds=settings.access_token_ttl_s)).timestamp()),
        "jti": str(uuid7()),
    }
    return jwt.encode(claims, settings.jwt_secret, algorithm=ALGORITHM)


@router.post("/oauth/token")
def token(
    grant_type: Annotated[str, Form()],
    client_id: Annotated[str, Form()],
    client_secret: Annotated[str, Form()],
    scope: Annotated[str | None, Form()] = None,
) -> JSONResponse:
    """RFC 6749 section 4.4. Errors use OAuth `error` format, not problem details."""
    if grant_type != "client_credentials":
        return _oauth_error(400, "unsupported_grant_type")
    with anonymous_tx() as conn:
        row = conn.execute(
            text("SELECT tenant_id, secret_hash, scopes FROM enterprise.lookup_api_client(:c)"),
            {"c": client_id},
        ).one_or_none()
    # Same error and same work for unknown client and wrong secret, so client ids cannot be probed
    # by response or by timing: an unknown id is checked against a dummy hash of equal cost.
    stored_hash = row.secret_hash if row is not None else _DUMMY_HASH
    if not verify_secret(client_secret, stored_hash) or row is None:
        return _oauth_error(401, "invalid_client")
    with tenant_tx(row.tenant_id) as conn:
        status = conn.execute(
            text("SELECT verification_status FROM enterprise.tenants WHERE id = :t"), {"t": row.tenant_id}
        ).scalar_one_or_none()
    if status is None or status == "SUSPENDED":
        return _oauth_error(401, "invalid_client")
    granted = frozenset(row.scopes)
    if scope:
        requested = frozenset(scope.split())
        if not requested <= granted:
            return _oauth_error(400, "invalid_scope")
        granted = requested
    now = datetime.now(UTC)
    return JSONResponse(
        {
            "access_token": issue_token(row.tenant_id, client_id, granted, now),
            "token_type": "Bearer",
            "expires_in": settings.access_token_ttl_s,
            "scope": " ".join(sorted(granted)),
        },
        headers={"Cache-Control": "no-store"},
    )


def _oauth_error(status: int, error: str) -> JSONResponse:
    return JSONResponse({"error": error}, status_code=status, headers={"Cache-Control": "no-store"})


def current_principal(authorization: Annotated[str | None, Header()] = None) -> Principal:
    if not authorization or not authorization.lower().startswith("bearer "):
        raise AuthError(401, "Missing bearer token")
    raw = authorization.split(" ", 1)[1]
    try:
        claims = jwt.decode(
            raw,
            settings.jwt_secret,
            algorithms=[ALGORITHM],
            audience=AUDIENCE,
            issuer=settings.jwt_issuer,
            leeway=30,
            options={"require": ["exp", "iat", "tid", "sub", "scope"]},
        )
        tenant_id = UUID(claims["tid"])
    except (jwt.PyJWTError, ValueError) as exc:
        raise AuthError(401, "Invalid or expired token") from exc
    if not client_still_active(claims["sub"], tenant_id):
        raise AuthError(401, "Client revoked or tenant suspended")
    return Principal(
        tenant_id=tenant_id, client_id=claims["sub"], scopes=frozenset(str(claims["scope"]).split())
    )


def client_still_active(client_id: str, tenant_id: UUID) -> bool:
    """Checked on every request, cached briefly per process."""
    now = time.monotonic()
    cached = _status_cache.get(client_id)
    if cached is not None and now - cached[0] < CLIENT_STATUS_TTL_S:
        return cached[1]
    with anonymous_tx() as conn:
        row = conn.execute(
            text("SELECT tenant_id FROM enterprise.lookup_api_client(:c)"), {"c": client_id}
        ).one_or_none()
    active = row is not None and row.tenant_id == tenant_id
    if active:
        with tenant_tx(tenant_id) as conn:
            status = conn.execute(
                text("SELECT verification_status FROM enterprise.tenants WHERE id = :t"), {"t": tenant_id}
            ).scalar_one_or_none()
        active = status is not None and status != "SUSPENDED"
    _status_cache[client_id] = (now, active)
    return active


def clear_client_status_cache() -> None:
    _status_cache.clear()


def require_scope(scope: str):  # type: ignore[no-untyped-def]
    def dep(principal: Annotated[Principal, Depends(current_principal)]) -> Principal:
        if scope not in principal.scopes:
            raise HTTPException(status_code=403, detail=f"Token lacks scope {scope}")
        return principal

    return dep
