"""MOCK OpenID Connect issuer for local development and tests. Never enabled outside local environment.

Implements just enough of authorization code flow with PKCE (S256) for the portal's real sign in code to
run unchanged against it. A production deployment points the portal at the bank's or RingSays' identity
provider instead (for example Microsoft Entra ID or Keycloak).

Key pair is generated per process, so tokens stop working when the API restarts (sign in again).
"""

from __future__ import annotations

import base64
import hashlib
import secrets
import threading
import time
from dataclasses import dataclass
from functools import lru_cache
from typing import Any

import jwt
from cryptography.hazmat.primitives.asymmetric import rsa

from app.core.config import DEV_OIDC_ISSUER, settings

CLIENT_ID = "ringsays-portal"
KID = "mock-1"
TOKEN_TTL_S = 3600
CODE_TTL_S = 60


@dataclass(frozen=True, slots=True)
class MockAccount:
    email: str
    name: str
    note: str


# Demo identities matching `app.scripts.seed` (MOCK DATA). Any other email can be typed on sign in page.
ACCOUNTS = [
    MockAccount("admin@mockbank.example", "Mock Bank Admin", "TENANT_ADMIN"),
    MockAccount("integration@mockbank.example", "Mock Bank Integration", "INTEGRATION_ADMIN"),
    MockAccount("supervisor@mockbank.example", "Mock Bank Supervisor", "SUPERVISOR"),
    MockAccount("agent@mockbank.example", "Mock Bank Agent", "AGENT"),
    MockAccount("compliance@mockbank.example", "Mock Bank Compliance", "COMPLIANCE"),
    MockAccount("reviewer@ringsays.example", "RingSays Reviewer", "RS_REVIEWER"),
    MockAccount("ops@ringsays.example", "RingSays Operations", "RS_ADMIN"),
]


@dataclass(frozen=True, slots=True)
class _Code:
    email: str
    name: str
    redirect_uri: str
    code_challenge: str
    nonce: str | None
    expires: float


_codes: dict[str, _Code] = {}
_lock = threading.Lock()


@lru_cache(maxsize=1)
def _private_key() -> rsa.RSAPrivateKey:
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


def public_key() -> rsa.RSAPublicKey:
    return _private_key().public_key()


def jwks() -> dict[str, Any]:
    jwk = jwt.algorithms.RSAAlgorithm.to_jwk(public_key(), as_dict=True)
    return {"keys": [{**jwk, "kid": KID, "use": "sig", "alg": "RS256"}]}


def subject_for(email: str) -> str:
    return "mock|" + hashlib.sha256(email.lower().encode()).hexdigest()[:24]


def redirect_allowed(uri: str) -> bool:
    return uri in settings.portal_redirect_uris


def issue_code(email: str, name: str, redirect_uri: str, code_challenge: str, nonce: str | None) -> str:
    code = secrets.token_urlsafe(24)
    with _lock:
        now = time.time()
        for k in [k for k, v in _codes.items() if v.expires < now]:
            del _codes[k]
        _codes[code] = _Code(email.lower(), name, redirect_uri, code_challenge, nonce, now + CODE_TTL_S)
    return code


class TokenError(Exception):
    pass


def exchange(code: str, redirect_uri: str, client_id: str, code_verifier: str) -> dict[str, Any]:
    with _lock:
        entry = _codes.pop(code, None)
    if entry is None or entry.expires < time.time():
        raise TokenError("invalid_grant")
    if client_id != CLIENT_ID or redirect_uri != entry.redirect_uri:
        raise TokenError("invalid_grant")
    digest = base64.urlsafe_b64encode(hashlib.sha256(code_verifier.encode()).digest()).rstrip(b"=").decode()
    if not secrets.compare_digest(digest, entry.code_challenge):
        raise TokenError("invalid_grant")
    return tokens_for(entry.email, entry.name, entry.nonce)


def tokens_for(email: str, name: str | None = None, nonce: str | None = None) -> dict[str, Any]:
    """Also used by tests to sign in as any email."""
    now = int(time.time())
    base = {
        "iss": DEV_OIDC_ISSUER,
        "sub": subject_for(email),
        "iat": now,
        "exp": now + TOKEN_TTL_S,
        "email": email.lower(),
        "email_verified": True,
        "name": name or email,
    }
    headers = {"kid": KID}
    access = jwt.encode(
        {**base, "aud": settings.admin_oidc_audience, "scope": "openid email profile"},
        _private_key(),
        algorithm="RS256",
        headers=headers,
    )
    id_claims = {**base, "aud": CLIENT_ID}
    if nonce:
        id_claims["nonce"] = nonce
    id_token = jwt.encode(id_claims, _private_key(), algorithm="RS256", headers=headers)
    return {
        "access_token": access,
        "id_token": id_token,
        "token_type": "Bearer",
        "expires_in": TOKEN_TTL_S,
        "scope": "openid email profile",
    }
