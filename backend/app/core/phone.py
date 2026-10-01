"""Phone number protection. Phone numbers are matched by a keyed hash and stored encrypted only where
a user must be able to export them. Pepper and encryption key come from settings (KMS in production)."""

from __future__ import annotations

import hashlib
import hmac

from cryptography.fernet import Fernet

from app.core.config import settings


def phone_hash(e164: str) -> str:
    return hmac.new(settings.phone_pepper.encode(), e164.encode(), hashlib.sha256).hexdigest()


def encrypt_phone(e164: str) -> str:
    return Fernet(settings.webhook_secret_key.encode()).encrypt(e164.encode()).decode()


def decrypt_phone(ciphertext: str) -> str:
    return Fernet(settings.webhook_secret_key.encode()).decrypt(ciphertext.encode()).decode()


def mask(e164: str) -> str:
    return e164[:4] + "*" * max(0, len(e164) - 7) + e164[-3:]
