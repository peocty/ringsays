"""RingSays user sign in: phone one time code, device registration, access and refresh tokens.

- One time codes: 6 digits, 5 minute expiry, 5 attempts, keyed hash stored, phone kept only encrypted
  until the code is used. Same response whether or not the number has an account.
- Devices: each sign in registers a device with a P-256 public key generated on the phone.
- Access tokens: 15 minute JWT bound to user and device.
- Refresh tokens: 30 days, rotated on every use, and each refresh must be signed by the device's private
  key. Reusing an already rotated token revokes the whole token family (theft signal).
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import logging
import secrets
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Protocol
from uuid import UUID, uuid4

import jwt
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from sqlalchemy import Connection, insert, select, text, update

from app.core.config import settings
from app.core.db import anonymous_tx, user_tx
from app.core.ids import uuid7
from app.core.phone import encrypt_phone, phone_hash
from app.core.tables import devices, otp_challenges, refresh_tokens, users
from app.modules.intent.errors import IntentError, RuleViolation
from app.platform import ratelimit

log = logging.getLogger(__name__)
OTP_TTL = timedelta(minutes=5)
OTP_MAX_ATTEMPTS = 5
ACCESS_TTL_S = 900
REFRESH_TTL = timedelta(days=30)
CLIENT_AUDIENCE = "ringsays-client"
ALGORITHM = "HS256"
DEVICE_CACHE_TTL_S = 5.0


class AuthFailed(IntentError):
    code = "invalid_grant"
    http_status = 401


# SMS


class SmsSender(Protocol):
    def send(self, phone_e164: str, message: str) -> None: ...


@dataclass
class MockSmsSender:
    """MOCK: records messages instead of sending. Tests read codes from here."""

    sent: list[tuple[str, str]] = field(default_factory=list)

    def send(self, phone_e164: str, message: str) -> None:
        self.sent.append((phone_e164, message))

    def last_code_for(self, phone_e164: str) -> str:
        for phone, msg in reversed(self.sent):
            if phone == phone_e164:
                return msg.split(":")[1].strip().split()[0]
        raise LookupError("no code sent")


_sms: SmsSender = MockSmsSender()


def get_sms() -> SmsSender:
    return _sms


def configure_sms(sender: SmsSender) -> None:
    global _sms
    _sms = sender


# One time codes


def _code_hash(challenge_id: UUID, code: str) -> str:
    return hmac.new(
        settings.phone_pepper.encode(), f"{challenge_id}:{code}".encode(), hashlib.sha256
    ).hexdigest()


def request_otp(phone: str, locale: str, now: datetime) -> UUID:
    """New code for a phone. Any earlier unused code for the same phone stops working."""
    challenge_id = uuid4()
    code = f"{secrets.randbelow(10**6):06d}"
    ph = phone_hash(phone)
    with anonymous_tx() as conn:
        conn.execute(
            update(otp_challenges)
            .where(otp_challenges.c.phone_hash == ph, otp_challenges.c.consumed_at.is_(None))
            .values(consumed_at=now, phone_ciphertext="")
        )
        conn.execute(
            insert(otp_challenges).values(
                id=challenge_id,
                phone_hash=ph,
                phone_ciphertext=encrypt_phone(phone),
                code_hash=_code_hash(challenge_id, code),
                created_at=now,
                expires_at=now + OTP_TTL,
                attempts=0,
            )
        )
    message = (
        f"رمز RingSays: {code} . لا تشاركه مع أحد"
        if locale == "ar"
        else f"RingSays code: {code} . Never share it with anyone"
    )
    get_sms().send(phone, message)
    return challenge_id


@dataclass(frozen=True, slots=True)
class DeviceIn:
    platform: str
    public_key: str
    app_version: str
    apns: str | None = None
    pushkit: str | None = None
    fcm: str | None = None


@dataclass(frozen=True, slots=True)
class Tokens:
    access_token: str
    refresh_token: str
    expires_in: int
    user_id: UUID
    device_id: UUID


def _load_public_key(b64: str) -> ec.EllipticCurvePublicKey:
    try:
        key = serialization.load_der_public_key(base64.b64decode(b64, validate=True))
    except (ValueError, TypeError) as exc:
        raise RuleViolation("device public_key must be base64 DER P-256") from exc
    if not isinstance(key, ec.EllipticCurvePublicKey) or not isinstance(key.curve, ec.SECP256R1):
        raise RuleViolation("device public_key must be P-256")
    return key


def verify_otp(challenge_id: UUID, code: str, device: DeviceIn, now: datetime) -> Tokens:
    _load_public_key(device.public_key)
    with anonymous_tx() as conn:
        row = conn.execute(
            select(otp_challenges).where(otp_challenges.c.id == challenge_id).with_for_update()
        ).one_or_none()
        if row is None or row.consumed_at is not None or row.expires_at <= now:
            raise AuthFailed("Code expired or already used; request a new one")
        if row.attempts >= OTP_MAX_ATTEMPTS:
            raise AuthFailed("Too many attempts; request a new code")
        limiter = ratelimit.get_limiter()
        limiter.check_otp_failures(row.phone_hash, now)  # daily cap on wrong codes per phone, across codes
        good = hmac.compare_digest(row.code_hash, _code_hash(challenge_id, code))
        conn.execute(
            update(otp_challenges)
            .where(otp_challenges.c.id == challenge_id)
            .values(
                attempts=row.attempts + 1,
                consumed_at=now if good else None,
                phone_ciphertext="" if good else row.phone_ciphertext,
            )
        )
        user_id = (
            conn.execute(
                text("SELECT identity.user_for_phone(:h, :c, :n)"),
                {"h": row.phone_hash, "c": row.phone_ciphertext, "n": now},
            ).scalar_one()
            if good
            else None
        )
    if user_id is None:  # attempt counted and committed above
        limiter.record_otp_failure(row.phone_hash, now)
        raise AuthFailed("Incorrect code")
    device_id = uuid4()
    with user_tx(user_id, row.phone_hash, None) as conn:
        since = conn.execute(select(users.c.created_at).where(users.c.id == user_id)).scalar_one()
        conn.execute(
            insert(devices).values(
                id=device_id,
                user_id=user_id,
                platform=device.platform,
                public_key=device.public_key,
                app_version=device.app_version,
                apns_token=device.apns,
                pushkit_token=device.pushkit,
                fcm_token=device.fcm,
                created_at=now,
            )
        )
        refresh = _issue_refresh(conn, user_id, device_id, uuid4(), now)
    return Tokens(
        _access(user_id, device_id, row.phone_hash, since), refresh, ACCESS_TTL_S, user_id, device_id
    )


def _access(user_id: UUID, device_id: UUID, ph: str, since: datetime) -> str:
    # Token times always use the real clock (business time may be simulated in tests and replays).
    issued = int(time.time())
    claims = {
        "iss": settings.jwt_issuer,
        "aud": CLIENT_AUDIENCE,
        "sub": str(user_id),
        "did": str(device_id),
        "ph": ph,
        "usc": since.isoformat(),
        "iat": issued,
        "exp": issued + ACCESS_TTL_S,
        "jti": str(uuid7()),
    }
    return jwt.encode(claims, settings.jwt_secret, algorithm=ALGORITHM)


def _issue_refresh(conn: Connection, user_id: UUID, device_id: UUID, family: UUID, now: datetime) -> str:
    token = f"rt_{user_id.hex}.{secrets.token_urlsafe(32)}"
    conn.execute(
        insert(refresh_tokens).values(
            token_hash=hashlib.sha256(token.encode()).hexdigest(),
            user_id=user_id,
            device_id=device_id,
            family_id=family,
            issued_at=now,
            expires_at=now + REFRESH_TTL,
        )
    )
    return token


def refresh(token: str, device_signature: str, now: datetime) -> Tokens:
    try:
        user_id = UUID(hex=token.split(".", 1)[0].removeprefix("rt_"))
    except ValueError as exc:
        raise AuthFailed("Invalid refresh token") from exc
    with user_tx(user_id, "", None) as conn:  # own user row only; no intent access
        ph = conn.execute(
            select(users.c.phone_hash, users.c.erased_at, users.c.created_at).where(users.c.id == user_id)
        ).one_or_none()
    if ph is None or ph.erased_at is not None:
        raise AuthFailed("Invalid refresh token")
    reuse = False
    with user_tx(user_id, ph.phone_hash, None) as conn:
        row = conn.execute(
            select(refresh_tokens)
            .where(refresh_tokens.c.token_hash == hashlib.sha256(token.encode()).hexdigest())
            .with_for_update()
        ).one_or_none()
        if row is None:
            raise AuthFailed("Invalid refresh token")
        if row.rotated_at is not None or row.revoked_at is not None:
            conn.execute(
                update(refresh_tokens)
                .where(refresh_tokens.c.family_id == row.family_id, refresh_tokens.c.revoked_at.is_(None))
                .values(revoked_at=now)
            )
            reuse = True
        else:
            if row.expires_at <= now:
                raise AuthFailed("Refresh token expired; sign in again")
            dev = conn.execute(select(devices).where(devices.c.id == row.device_id)).one()
            if dev.revoked_at is not None:
                raise AuthFailed("Device signed out")
            if not _signature_ok(dev.public_key, token, device_signature):
                raise AuthFailed("Device signature invalid")
            conn.execute(
                update(refresh_tokens)
                .where(refresh_tokens.c.token_hash == row.token_hash)
                .values(rotated_at=now)
            )
            new_refresh = _issue_refresh(conn, user_id, row.device_id, row.family_id, now)
            device_id = row.device_id
    if reuse:
        # Committed revocation above; tell caller after commit.
        raise AuthFailed("Refresh token reuse detected; all sessions on this device signed out")
    return Tokens(
        # Visibility starts at account creation, never at refresh time (ADR 0009).
        _access(user_id, device_id, ph.phone_hash, ph.created_at),
        new_refresh,
        ACCESS_TTL_S,
        user_id,
        device_id,
    )


def _signature_ok(public_key_b64: str, token: str, signature_b64: str) -> bool:
    try:
        key = _load_public_key(public_key_b64)
        key.verify(base64.b64decode(signature_b64, validate=True), token.encode(), ec.ECDSA(hashes.SHA256()))
    except (InvalidSignature, ValueError, TypeError, RuleViolation):
        return False
    return True


# Access token verification


@dataclass(frozen=True, slots=True)
class ClientPrincipal:
    user_id: UUID
    device_id: UUID
    phone_hash: str
    since: datetime  # account creation; earlier intents to this number are not this user's

    @property
    def actor(self) -> str:
        return f"user:{self.user_id}"


_device_cache: dict[UUID, tuple[float, bool]] = {}


def decode_access(raw: str) -> ClientPrincipal:
    try:
        claims = jwt.decode(
            raw,
            settings.jwt_secret,
            algorithms=[ALGORITHM],
            audience=CLIENT_AUDIENCE,
            issuer=settings.jwt_issuer,
            leeway=30,
            options={"require": ["exp", "iat", "sub", "did", "ph", "usc"]},
        )
        principal = ClientPrincipal(
            UUID(claims["sub"]), UUID(claims["did"]), str(claims["ph"]), datetime.fromisoformat(claims["usc"])
        )
    except (jwt.PyJWTError, ValueError) as exc:
        raise AuthFailed("Invalid or expired token") from exc
    if not _device_active(principal):
        raise AuthFailed("Device signed out")
    return principal


def _device_active(p: ClientPrincipal) -> bool:
    # Shared revocation list first, so sign out and erasure take effect on every API process at once.
    try:
        if ratelimit.get_limiter().is_revoked(f"device:{p.device_id}"):
            return False
    except Exception:  # Redis down: fall through to database, skipping cache
        _device_cache.pop(p.device_id, None)
    cached = _device_cache.get(p.device_id)
    mono = time.monotonic()
    if cached is not None and mono - cached[0] < DEVICE_CACHE_TTL_S:
        return cached[1]
    with user_tx(p.user_id, p.phone_hash, None) as conn:
        row = conn.execute(
            select(devices.c.revoked_at, users.c.erased_at)
            .join(users, users.c.id == devices.c.user_id)
            .where(devices.c.id == p.device_id)
        ).one_or_none()
    active = row is not None and row.revoked_at is None and row.erased_at is None
    _device_cache[p.device_id] = (mono, active)
    return active


def clear_device_cache() -> None:
    _device_cache.clear()


def revoke_devices_everywhere(device_ids: list[UUID]) -> None:
    """Publish revocations to every API process (Redis), for the lifetime of any issued access token."""
    for d in device_ids:
        _device_cache.pop(d, None)
        try:
            ratelimit.get_limiter().mark_revoked(f"device:{d}", ACCESS_TTL_S + 60)
        except Exception as exc:  # database check (5 s cache) remains the fallback
            log.warning("could not publish device revocation: %s", type(exc).__name__)


def update_push_tokens(
    p: ClientPrincipal, device_id: UUID, apns: str | None, pushkit: str | None, fcm: str | None
) -> bool:
    if device_id != p.device_id:
        return False
    with user_tx(p.user_id, p.phone_hash, None) as conn:
        result = conn.execute(
            update(devices)
            .where(devices.c.id == device_id, devices.c.revoked_at.is_(None))
            .values(apns_token=apns, pushkit_token=pushkit, fcm_token=fcm)
        )
    return result.rowcount == 1
