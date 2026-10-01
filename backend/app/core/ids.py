"""Identifier helpers. UUIDv7 (RFC 9562): time ordered, safe to expose, index friendly."""

from __future__ import annotations

import secrets
import time
import uuid


def uuid7(unix_ms: int | None = None) -> uuid.UUID:
    ms = int(time.time() * 1000) if unix_ms is None else unix_ms
    if not 0 <= ms < 1 << 48:
        raise ValueError("timestamp out of range for UUIDv7")
    rand_a = secrets.randbits(12)
    rand_b = secrets.randbits(62)
    value = (ms << 80) | (0x7 << 76) | (rand_a << 64) | (0b10 << 62) | rand_b
    return uuid.UUID(int=value)
