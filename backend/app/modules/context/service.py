"""Context Tokens: short lived, single device handles to an intent's display payload.

Token is 128 bits of randomness, shown once to the tenant (who passes it to its own app through the SDK).
Only a SHA-256 of it is stored. First resolve binds it to one device; resolves from any other device,
after expiry, or after revocation are refused. Tokens are revoked when an intent reaches a terminal status.
"""

from __future__ import annotations

import hashlib
import secrets
from datetime import datetime
from uuid import UUID

from sqlalchemy import Connection, insert, text, update

from app.core.tables import context_tokens
from app.modules.intent.domain import Intent
from app.modules.intent.errors import IntentError


class TokenRefused(IntentError):
    code = "context_token_refused"
    http_status = 403


class TokenGone(IntentError):
    code = "context_token_gone"
    http_status = 410


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def issue(conn: Connection, intent: Intent, now: datetime) -> str:
    """Create token for an intent inside tenant transaction. Expires with intent validity."""
    if intent.tenant_id is None:
        raise ValueError("context tokens are issued for enterprise intents only")
    token = secrets.token_urlsafe(16)  # 128 bits, 22 characters
    conn.execute(
        insert(context_tokens).values(
            token_hash=_hash(token),
            tenant_id=intent.tenant_id,
            intent_id=intent.intent_id,
            issued_at=now,
            expires_at=intent.valid_until,
            resolve_count=0,
        )
    )
    return token


def resolve(conn: Connection, token: str, device_id: UUID, now: datetime) -> tuple[UUID, UUID]:
    """Resolve from a device without tenant scope. Returns (tenant_id, intent_id) or raises.

    Unknown and foreign-device tokens get the same 403, so a token holder learns nothing extra.
    """
    row = conn.execute(
        text("SELECT tenant_id, intent_id, outcome FROM context.resolve_token(:h, :d, :n)"),
        {"h": _hash(token), "d": device_id, "n": now},
    ).one()
    if row.outcome == "ok":
        return row.tenant_id, row.intent_id
    if row.outcome in ("expired", "revoked"):
        raise TokenGone("Context token has expired or been revoked")
    raise TokenRefused("Context token not valid for this device")


def follow_validity(conn: Connection, intent_id: UUID, valid_until: datetime) -> int:
    """A later agreed time (LATER, PROPOSE, SCHEDULE) extends the intent; its token lasts as long."""
    result = conn.execute(
        update(context_tokens)
        .where(
            context_tokens.c.intent_id == intent_id,
            context_tokens.c.revoked_at.is_(None),
            context_tokens.c.expires_at < valid_until,
        )
        .values(expires_at=valid_until)
    )
    return int(result.rowcount or 0)


def revoke_for_intent(conn: Connection, intent_id: UUID, now: datetime) -> int:
    result = conn.execute(
        update(context_tokens)
        .where(context_tokens.c.intent_id == intent_id, context_tokens.c.revoked_at.is_(None))
        .values(revoked_at=now)
    )
    return int(result.rowcount or 0)
