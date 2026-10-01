"""OpenID Connect token verification for the portal and back office.

The identity provider proves who the person is. RingSays decides what they may do (roles are stored by
RingSays, see `app.modules.admin.access`). Tokens must be signed with an asymmetric key (RS256 or ES256)
published in the issuer's JWKS, carry our audience, and be unexpired.

Local development uses the MOCK issuer in `app.modules.devoidc`, whose key is read in process.
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from typing import Any

import httpx
import jwt

from app.core.config import DEV_OIDC_ISSUER, settings

ALGORITHMS = ["RS256", "ES256"]


@dataclass(frozen=True, slots=True)
class Identity:
    issuer: str
    subject: str
    email: str | None
    email_verified: bool
    name: str | None


class InvalidIdentityToken(Exception):
    pass


@lru_cache(maxsize=8)
def _jwks_client(jwks_url: str) -> jwt.PyJWKClient:
    return jwt.PyJWKClient(jwks_url, cache_keys=True, lifespan=600, timeout=5)


@lru_cache(maxsize=8)
def _discover_jwks_url(issuer: str) -> str:
    r = httpx.get(issuer.rstrip("/") + "/.well-known/openid-configuration", timeout=5)
    r.raise_for_status()
    doc = r.json()
    if doc.get("issuer") != issuer:
        raise InvalidIdentityToken("issuer discovery document does not match issuer")
    return str(doc["jwks_uri"])


def _signing_key(token: str, issuer: str, jwks_url: str | None) -> Any:
    if issuer == DEV_OIDC_ISSUER:
        if not settings.dev_oidc_enabled:
            raise InvalidIdentityToken("MOCK issuer is disabled")
        from app.modules.devoidc import issuer as dev

        return dev.public_key()
    return _jwks_client(jwks_url or _discover_jwks_url(issuer)).get_signing_key_from_jwt(token).key


def trusted_issuers() -> dict[str, str | None]:
    return {
        settings.admin_oidc_issuer: settings.admin_oidc_jwks_url,
        settings.staff_oidc_issuer: settings.staff_oidc_jwks_url,
    }


def verify(token: str) -> Identity:
    """Verify signature, issuer, audience and expiry. Raises InvalidIdentityToken."""
    try:
        unverified = jwt.decode(token, options={"verify_signature": False})
        issuer = unverified.get("iss")
        issuers = trusted_issuers()
        if not isinstance(issuer, str) or issuer not in issuers:
            raise InvalidIdentityToken("untrusted issuer")
        claims = jwt.decode(
            token,
            _signing_key(token, issuer, issuers[issuer]),
            algorithms=ALGORITHMS,
            audience=settings.admin_oidc_audience,
            issuer=issuer,
            leeway=30,
            options={"require": ["exp", "iat", "iss", "sub", "aud"]},
        )
    except InvalidIdentityToken:
        raise
    except (jwt.PyJWTError, httpx.HTTPError, KeyError, ValueError) as exc:
        raise InvalidIdentityToken("invalid or expired token") from exc
    email = claims.get("email")
    return Identity(
        issuer=issuer,
        subject=str(claims["sub"]),
        email=email.lower() if isinstance(email, str) else None,
        email_verified=claims.get("email_verified") is True,
        name=claims.get("name") if isinstance(claims.get("name"), str) else None,
    )
