"""Rate limits and contact policy, enforced atomically in Redis before an intent is created.

Limits (defaults in settings, per tenant overrides later):
- creates per tenant per minute (protects platform)
- URGENT intents per tenant per day (stops urgent abuse; spec section 22)
- intents per recipient per tenant per day (contact frequency policy for collections teams)

Phone numbers are keyed with HMAC and a pepper, so Redis holds no phone number.
All counters are checked first and incremented together in one Lua script: a refused request
consumes nothing. If Redis is unreachable the limiter fails open and logs, so a cache outage does not
stop a bank's operations, except for URGENT intents, which fail closed (503) because URGENT may pass
receiver quiet hours only while its quota is enforced.
"""

from __future__ import annotations

import hashlib
import hmac
import logging
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import UUID

import redis

from app.core.config import settings
from app.modules.intent.domain import Priority
from app.modules.intent.errors import IntentError

log = logging.getLogger(__name__)

# KEYS: counter keys. ARGV: n, then limits[n], then ttls[n]. Returns 0 if allowed, else 1-based index
# of first exceeded counter.
_SCRIPT = """
local n = tonumber(ARGV[1])
for i = 1, n do
  local current = tonumber(redis.call('GET', KEYS[i]) or '0')
  if current >= tonumber(ARGV[1 + i]) then return i end
end
for i = 1, n do
  local v = redis.call('INCR', KEYS[i])
  if v == 1 then redis.call('EXPIRE', KEYS[i], tonumber(ARGV[1 + n + i])) end
end
return 0
"""


class RateLimited(IntentError):
    code = "rate_limited"
    http_status = 429

    def __init__(self, detail: str, retry_after_s: int) -> None:
        super().__init__(detail)
        self.retry_after_s = retry_after_s


class LimiterUnavailable(RateLimited):
    code = "rate_limit_unavailable"
    http_status = 503


@dataclass(frozen=True, slots=True)
class _Counter:
    key: str
    limit: int
    ttl_s: int
    message: str
    retry_after_s: int


def phone_key(phone: str) -> str:
    return hmac.new(settings.phone_pepper.encode(), phone.encode(), hashlib.sha256).hexdigest()[:32]


def _until_midnight_utc(now: datetime) -> int:
    tomorrow = (now + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
    return max(1, int((tomorrow - now).total_seconds()))


class Limiter:
    def ping(self) -> None:
        """Raises when Redis is unreachable (readiness probe)."""
        self._r.ping()

    def __init__(self, client: redis.Redis) -> None:
        self._r = client
        self._script = client.register_script(_SCRIPT)

    def check_create(self, tenant_id: UUID, priority: Priority, to_phone: str, now: datetime) -> None:
        now = now.astimezone(UTC)
        minute = now.strftime("%Y%m%d%H%M")
        day = now.strftime("%Y%m%d")
        midnight = _until_midnight_utc(now)
        counters = [
            _Counter(
                f"rl:create:{tenant_id}:{minute}",
                settings.tenant_creates_per_minute,
                120,
                "tenant intent rate exceeded",
                60 - now.second,
            ),
            _Counter(
                f"rl:recipient:{tenant_id}:{phone_key(to_phone)}:{day}",
                settings.recipient_intents_per_tenant_per_day,
                midnight + 60,
                "daily contact limit for this recipient reached",
                midnight,
            ),
        ]
        if priority is Priority.URGENT:
            counters.append(
                _Counter(
                    f"rl:urgent:{tenant_id}:{day}",
                    settings.tenant_urgent_per_day,
                    midnight + 60,
                    "daily URGENT quota reached",
                    midnight,
                )
            )
        args: list[int] = [len(counters), *(c.limit for c in counters), *(c.ttl_s for c in counters)]
        try:
            exceeded = int(self._script(keys=[c.key for c in counters], args=args))
        except redis.RedisError as exc:
            if priority is Priority.URGENT:
                # URGENT bypasses receiver quiet hours only because its daily quota is enforced.
                log.error("rate limiter unavailable, refusing URGENT: %s", type(exc).__name__)
                raise LimiterUnavailable("URGENT quota cannot be checked right now; retry", 30) from exc
            log.error("rate limiter unavailable, failing open: %s", type(exc).__name__)
            return
        if exceeded:
            c = counters[exceeded - 1]
            raise RateLimited(c.message, c.retry_after_s)

    def check_otp(self, phone_hash: str, client_ip: str, now: datetime) -> None:
        """Code requests: per phone per hour, per client address per hour, and platform wide per minute.
        Fails closed: SMS pumping costs money and floods customers."""
        now = now.astimezone(UTC)
        hour, minute = now.strftime("%Y%m%d%H"), now.strftime("%Y%m%d%H%M")
        ip_key = hmac.new(settings.phone_pepper.encode(), client_ip.encode(), hashlib.sha256).hexdigest()[:24]
        counters = [
            (
                f"rl:otp:{phone_hash[:32]}:{hour}",
                settings.otp_per_phone_per_hour,
                3700,
                "Too many codes requested for this number; try later",
            ),
            (
                f"rl:otpip:{ip_key}:{hour}",
                settings.otp_per_ip_per_hour,
                3700,
                "Too many sign in attempts from this network; try later",
            ),
            (f"rl:otpall:{minute}", settings.otp_global_per_minute, 120, "Sign in is busy; retry shortly"),
        ]
        args: list[int] = [len(counters), *(c[1] for c in counters), *(c[2] for c in counters)]
        try:
            exceeded = int(self._script(keys=[c[0] for c in counters], args=args))
        except redis.RedisError as exc:
            log.error("rate limiter unavailable, refusing OTP: %s", type(exc).__name__)
            raise LimiterUnavailable("Sign in temporarily unavailable; retry shortly", 30) from exc
        if exceeded:
            raise RateLimited(counters[exceeded - 1][3], 3600 - now.minute * 60)

    def _fail_key(self, phone_hash: str, now: datetime) -> str:
        return f"rl:otpfail:{phone_hash[:32]}:{now.astimezone(UTC).strftime('%Y%m%d')}"

    def check_otp_failures(self, phone_hash: str, now: datetime) -> None:
        """Wrong codes per phone per day, counted across all codes. Locks sign in for the day when reached."""
        try:
            raw = self._r.get(self._fail_key(phone_hash, now))
            failures = int(raw) if isinstance(raw, (bytes, str, int)) else 0
        except redis.RedisError as exc:
            raise LimiterUnavailable("Sign in temporarily unavailable; retry shortly", 30) from exc
        if failures >= settings.otp_failures_per_phone_per_day:
            raise RateLimited("Too many wrong codes for this number today", _until_midnight_utc(now))

    def record_otp_failure(self, phone_hash: str, now: datetime) -> None:
        key = self._fail_key(phone_hash, now)
        try:
            pipe = self._r.pipeline()
            pipe.incr(key)
            pipe.expire(key, _until_midnight_utc(now) + 60)
            pipe.execute()
        except redis.RedisError as exc:
            log.error("could not record OTP failure: %s", type(exc).__name__)

    def mark_revoked(self, key: str, ttl_s: int) -> None:
        self._r.set(f"revoked:{key}", "1", ex=ttl_s)

    def is_revoked(self, key: str) -> bool:
        return bool(self._r.exists(f"revoked:{key}"))


_limiter: Limiter | None = None


def get_limiter() -> Limiter:
    global _limiter
    if _limiter is None:
        _limiter = Limiter(redis.Redis.from_url(settings.redis_url, socket_timeout=0.5))
    return _limiter


def set_limiter(limiter: Limiter | None) -> None:
    global _limiter
    _limiter = limiter
