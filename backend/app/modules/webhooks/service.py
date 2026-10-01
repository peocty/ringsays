"""Signed webhooks to tenant endpoints.

Queue rows are written in the same transaction as the intent change (so no event is lost or invented).
A worker dispatches them: per endpoint and intent strictly in order, HMAC SHA-256 signed, retried with
exponential backoff for 24 hours, then DEAD. Tenants can replay any delivery.

Signature header: `RingSays-Signature: t=<unix seconds>,v1=<hex HMAC-SHA256(secret, t + "." + body)>`.
Receivers must reject timestamps older than 300 seconds.
"""

from __future__ import annotations

import hashlib
import hmac
import ipaddress
import json
import logging
import secrets
import socket
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Protocol
from urllib.parse import urlsplit
from uuid import UUID, uuid4

from cryptography.fernet import Fernet
from sqlalchemy import Connection, Engine, and_, exists, insert, select, update
from sqlalchemy.orm import aliased

from app.core.config import settings
from app.core.db import worker_tx
from app.core.ids import uuid7
from app.core.tables import webhook_deliveries, webhook_endpoints
from app.modules.intent.domain import Intent, IntentEvent, IntentStatus
from app.modules.intent.errors import IntentError, RuleViolation

RETRY_WINDOW = timedelta(hours=24)
FIRST_BACKOFF_S = 30
MAX_BACKOFF_S = 3600
TIMEOUT_S = 5.0
MAX_ROUNDS = 10
PER_ENDPOINT_PER_RUN = 20
CLAIM_LEASE = timedelta(seconds=60)
log = logging.getLogger(__name__)

STATUS_TO_EVENT: dict[IntentStatus, str] = {
    IntentStatus.DELIVERED: "intent.delivered",
    IntentStatus.ACCEPTED: "intent.accepted",
    IntentStatus.RESCHEDULED: "intent.rescheduled",
    IntentStatus.SCHEDULED: "intent.scheduled",
    IntentStatus.DECLINED: "intent.declined",
    IntentStatus.EXPIRED: "intent.expired",
    IntentStatus.CANCELLED: "intent.cancelled",
    IntentStatus.COMPLETED: "outcome.recorded",
    IntentStatus.FOLLOW_UP_REQUIRED: "outcome.recorded",
}
ALL_EVENTS = frozenset(STATUS_TO_EVENT.values())


class DeliveryNotFound(IntentError):
    code = "not_found"
    http_status = 404


def _fernet() -> Fernet:
    return Fernet(settings.webhook_secret_key.encode())


def sign(secret: str, timestamp: int, body: bytes) -> str:
    mac = hmac.new(secret.encode(), f"{timestamp}.".encode() + body, hashlib.sha256).hexdigest()
    return f"t={timestamp},v1={mac}"


def verify_signature(secret: str, header: str, body: bytes, now_ts: int, tolerance_s: int = 300) -> bool:
    """Reference verifier for tenants (also used in tests)."""
    try:
        parts = dict(p.split("=", 1) for p in header.split(","))
        ts = int(parts["t"])
    except (ValueError, KeyError):
        return False
    if abs(now_ts - ts) > tolerance_s:
        return False
    return hmac.compare_digest(sign(secret, ts, body), header)


# Endpoint registration (called by admin API in stage 5; seed uses it now)


_NAT64 = ipaddress.ip_network("64:ff9b::/96")
_NAT64_LOCAL = ipaddress.ip_network("64:ff9b:1::/48")


def is_public_address(raw: str) -> bool:
    """True only for globally routable addresses, after unwrapping IPv6 forms that embed IPv4
    (mapped, compatible, 6to4, Teredo, NAT64), so an embedded private address is caught."""
    ip = ipaddress.ip_address(raw.split("%", 1)[0])
    if isinstance(ip, ipaddress.IPv6Address):
        if ip in _NAT64_LOCAL:
            return False
        embedded: list[ipaddress.IPv4Address] = []
        if ip.ipv4_mapped is not None:
            embedded.append(ip.ipv4_mapped)
        if ip.sixtofour is not None:
            embedded.append(ip.sixtofour)
        if ip.teredo is not None:
            embedded.extend(ip.teredo)
        if ip in _NAT64:
            embedded.append(ipaddress.IPv4Address(int(ip) & 0xFFFFFFFF))
        if int(ip) >> 32 == 0:  # IPv4 compatible ::a.b.c.d (deprecated, still routable by some stacks)
            embedded.append(ipaddress.IPv4Address(int(ip) & 0xFFFFFFFF))
        if any(not e.is_global for e in embedded):
            return False
    return bool(ip.is_global)


_LOOPBACK_HOSTS = frozenset({"127.0.0.1", "localhost"})


def is_loopback_url(url: str) -> bool:
    parts = urlsplit(url)
    return parts.scheme == "http" and (parts.hostname or "") in _LOOPBACK_HOSTS


def validate_url(url: str) -> None:
    parts = urlsplit(url)
    # Local environment only: a bank backend on the developer's machine (mock bank, stage 7).
    if settings.environment == "local" and is_loopback_url(url):
        if parts.username or parts.password:
            raise RuleViolation("webhook url must not contain credentials")
        if not parts.path.startswith("/"):
            raise RuleViolation("webhook url needs a path, for example /webhooks/ringsays")
        return
    if parts.scheme != "https" or not parts.hostname:
        raise RuleViolation("webhook url must be https with a host name")
    if parts.username or parts.password:
        raise RuleViolation("webhook url must not contain credentials")
    if parts.port not in (None, 443):
        raise RuleViolation("webhook url must use port 443")
    try:
        ipaddress.ip_address(parts.hostname)
    except ValueError:
        return
    if not is_public_address(parts.hostname):
        raise RuleViolation("webhook url must not point at a private or reserved address")


def create_endpoint(conn: Connection, tenant_id: UUID, url: str, events: list[str]) -> tuple[UUID, str]:
    """Returns (endpoint_id, secret). Secret is shown once; stored encrypted."""
    validate_url(url)
    unknown = set(events) - ALL_EVENTS
    if unknown or not events:
        raise RuleViolation(f"unknown webhook events: {sorted(unknown)}")
    endpoint_id = uuid4()
    secret = "whsec_" + secrets.token_urlsafe(32)
    conn.execute(
        insert(webhook_endpoints).values(
            id=endpoint_id,
            tenant_id=tenant_id,
            url=url,
            secret_ciphertext=_fernet().encrypt(secret.encode()).decode(),
            events=sorted(set(events)),
            active=True,
        )
    )
    return endpoint_id, secret


# Enqueue (inside intent transaction)


def _slot(s: Any) -> dict[str, str] | None:
    return None if s is None else {"start": s.start.isoformat(), "end": s.end.isoformat()}


def payload_for(intent: Intent, event: IntentEvent, event_type: str, event_id: UUID) -> dict[str, Any]:
    return {
        "event_id": str(event_id),
        "type": event_type,
        "occurred_at": event.at.isoformat(),
        "intent_id": str(intent.intent_id),
        "status": event.to_status.value,
        "channel_used": intent.channel_used.value if intent.channel_used else None,
        "slot": _slot(intent.scheduled_slot) if event.to_status is IntentStatus.SCHEDULED else None,
        "proposed_slots": [_slot(s) for s in intent.proposed_slots]
        if event.to_status is IntentStatus.RESCHEDULED
        else [],
        "decline_reason": intent.decline_reason.value
        if intent.decline_reason and event.to_status is IntentStatus.DECLINED
        else None,
        "outcome_code": intent.outcome_code.value
        if intent.outcome_code and event_type == "outcome.recorded"
        else None,
    }


def enqueue_for_event(conn: Connection, intent: Intent, event: IntentEvent, now: datetime) -> int:
    event_type = STATUS_TO_EVENT.get(event.to_status)
    if event_type is None or intent.tenant_id is None:
        return 0
    endpoints = (
        conn.execute(
            select(webhook_endpoints.c.id).where(
                webhook_endpoints.c.tenant_id == intent.tenant_id,
                webhook_endpoints.c.active.is_(True),
                webhook_endpoints.c.events.any(event_type),
            )
        )
        .scalars()
        .all()
    )
    event_id = uuid7()
    payload = payload_for(intent, event, event_type, event_id)
    for endpoint_id in endpoints:
        conn.execute(
            insert(webhook_deliveries).values(
                tenant_id=intent.tenant_id,
                endpoint_id=endpoint_id,
                event_id=event_id,
                event_type=event_type,
                intent_id=intent.intent_id,
                payload=payload,
                status="PENDING",
                attempts=0,
                next_attempt_at=now,
                created_at=now,
            )
        )
    return len(endpoints)


# Dispatch (worker)


@dataclass(frozen=True, slots=True)
class HttpResult:
    status: int | None
    error: str | None = None


class HttpSender(Protocol):
    def post(self, url: str, body: bytes, headers: dict[str, str]) -> HttpResult: ...


@dataclass
class MockHttpSender:
    """MOCK: records requests; `responses` maps url to a list of status codes returned in turn."""

    requests: list[tuple[str, bytes, dict[str, str]]] = field(default_factory=list)
    responses: dict[str, list[int]] = field(default_factory=dict)

    def post(self, url: str, body: bytes, headers: dict[str, str]) -> HttpResult:
        self.requests.append((url, body, headers))
        queue = self.responses.get(url)
        code = queue.pop(0) if queue else 200
        return HttpResult(status=code)


class HttpxSender:
    """Real sender. Resolves host once, refuses any non public address, then connects to that exact
    address (Host header and TLS SNI keep the original name, so certificates are still verified),
    which closes the DNS rebinding gap. Port 443 only, no redirects, 5 second limit per phase and the
    response body is never read. Not exercised here (sandbox has no egress).
    """

    def post(self, url: str, body: bytes, headers: dict[str, str]) -> HttpResult:
        import httpx

        parts = urlsplit(url)
        host = parts.hostname or ""
        try:
            infos = socket.getaddrinfo(host, 443, proto=socket.IPPROTO_TCP)
        except OSError as exc:
            return HttpResult(None, f"dns: {type(exc).__name__}")
        addresses = [str(i[4][0]) for i in infos]
        if not addresses or not all(is_public_address(a) for a in addresses):
            return HttpResult(None, "refused: target resolves to non public address")
        ip = addresses[0]
        netloc = f"[{ip}]" if ":" in ip else ip
        pinned = parts._replace(netloc=netloc).geturl()
        timeout = httpx.Timeout(connect=TIMEOUT_S, read=TIMEOUT_S, write=TIMEOUT_S, pool=TIMEOUT_S)
        try:
            with httpx.Client(timeout=timeout, follow_redirects=False) as client:
                request = client.build_request(
                    "POST",
                    pinned,
                    content=body,
                    headers={**headers, "Host": host},
                    extensions={"sni_hostname": host},
                )
                response = client.send(request, stream=True)
                response.close()
        except httpx.HTTPError as exc:
            return HttpResult(None, f"http: {type(exc).__name__}")
        return HttpResult(response.status_code)


class LoopbackHttpSender:
    """Local environment only: delivers to a receiver on this machine (http allowed, any port).
    Anything not loopback goes to `other` (MOCK in local). Refuses everything outside local."""

    def __init__(self, other: HttpSender) -> None:
        self.other = other

    def post(self, url: str, body: bytes, headers: dict[str, str]) -> HttpResult:
        if not is_loopback_url(url):
            return self.other.post(url, body, headers)
        if settings.environment != "local":
            return HttpResult(None, "refused: loopback delivery is local only")
        import httpx

        timeout = httpx.Timeout(TIMEOUT_S)
        try:
            with httpx.Client(timeout=timeout, follow_redirects=False, trust_env=False) as client:
                response = client.post(url, content=body, headers=headers)
        except httpx.HTTPError as exc:
            return HttpResult(None, f"http: {type(exc).__name__}")
        return HttpResult(response.status_code)


def backoff(attempts: int) -> timedelta:
    return timedelta(seconds=min(MAX_BACKOFF_S, FIRST_BACKOFF_S * 2 ** max(0, attempts - 1)))


def dispatch_due(
    now: datetime,
    sender: HttpSender,
    *,
    batch: int = 200,
    per_endpoint: int = PER_ENDPOINT_PER_RUN,
    engine: Engine | None = None,
) -> int:
    """Send due deliveries. Only the oldest pending delivery per (endpoint, intent) is eligible, so a
    tenant never receives `intent.scheduled` before the `intent.delivered` that preceded it.

    Each delivery is claimed with a short lease and committed before the HTTP call, so no database lock
    is held while waiting on a tenant server, and a crashed worker's claim expires. Each endpoint gets at
    most `per_endpoint` sends per run, so one slow tenant cannot starve others.
    """
    earlier = aliased(webhook_deliveries)
    blocked = exists().where(
        and_(
            earlier.c.endpoint_id == webhook_deliveries.c.endpoint_id,
            earlier.c.intent_id == webhook_deliveries.c.intent_id,
            earlier.c.status == "PENDING",
            earlier.c.id < webhook_deliveries.c.id,
        )
    )
    sent = 0
    used: dict[UUID, int] = {}
    # Rounds let the next event for an intent go out in the same run once the earlier one succeeded.
    for _ in range(MAX_ROUNDS):
        with worker_tx(engine) as conn:
            rows = conn.execute(
                select(webhook_deliveries.c.id, webhook_deliveries.c.endpoint_id)
                .where(
                    webhook_deliveries.c.status == "PENDING",
                    webhook_deliveries.c.next_attempt_at <= now,
                    ~blocked,
                )
                .order_by(webhook_deliveries.c.id)
                .limit(batch)
            ).all()
        rows = [r for r in rows if used.get(r.endpoint_id, 0) < per_endpoint]
        if not rows:
            break
        round_sent = 0
        for r in rows:
            if used.get(r.endpoint_id, 0) >= per_endpoint:
                continue
            used[r.endpoint_id] = used.get(r.endpoint_id, 0) + 1
            try:
                round_sent += _dispatch_one(r.id, now, sender, engine)
            except Exception as exc:
                log.error("webhook delivery %s failed: %s", r.id, type(exc).__name__)
                _mark_error(r.id, now, type(exc).__name__, engine)
        sent += round_sent
        if round_sent == 0:
            break
    return sent


def _claim(conn: Connection, delivery_id: int, now: datetime) -> Any:
    row = conn.execute(
        select(
            webhook_deliveries,
            webhook_endpoints.c.url,
            webhook_endpoints.c.secret_ciphertext,
            webhook_endpoints.c.active,
        )
        .join(webhook_endpoints, webhook_endpoints.c.id == webhook_deliveries.c.endpoint_id)
        .where(
            webhook_deliveries.c.id == delivery_id,
            webhook_deliveries.c.status == "PENDING",
            webhook_deliveries.c.next_attempt_at <= now,
        )
        .with_for_update(of=webhook_deliveries, skip_locked=True)
    ).one_or_none()
    if row is not None:
        conn.execute(
            update(webhook_deliveries)
            .where(webhook_deliveries.c.id == delivery_id)
            .values(next_attempt_at=now + CLAIM_LEASE)
        )
    return row


def _dispatch_one(delivery_id: int, now: datetime, sender: HttpSender, engine: Engine | None) -> int:
    with worker_tx(engine) as conn:
        row = _claim(conn, delivery_id, now)
    if row is None:
        return 0
    attempts = row.attempts + 1
    if not row.active:
        with worker_tx(engine) as conn:
            conn.execute(
                update(webhook_deliveries)
                .where(webhook_deliveries.c.id == delivery_id)
                .values(status="DEAD", attempts=attempts, last_error="endpoint disabled")
            )
        return 0
    secret = _fernet().decrypt(row.secret_ciphertext.encode()).decode()
    body = json.dumps(row.payload, separators=(",", ":"), sort_keys=True).encode()
    headers = {
        "Content-Type": "application/json",
        "User-Agent": "RingSays-Webhooks/1",
        "RingSays-Event-Id": str(row.event_id),
        "RingSays-Event-Type": row.event_type,
        # Signed with wall clock at send time, so a long worker run never sends stale timestamps.
        "RingSays-Signature": sign(secret, int(time.time()), body),
    }
    result = sender.post(row.url, body, headers)
    with worker_tx(engine) as conn:
        if result.status is not None and 200 <= result.status < 300:
            conn.execute(
                update(webhook_deliveries)
                .where(webhook_deliveries.c.id == delivery_id)
                .values(
                    status="DELIVERED",
                    attempts=attempts,
                    last_status_code=result.status,
                    last_error=None,
                    delivered_at=now,
                )
            )
            return 1
        next_at = now + backoff(attempts)
        dead = next_at > row.created_at + RETRY_WINDOW
        conn.execute(
            update(webhook_deliveries)
            .where(webhook_deliveries.c.id == delivery_id)
            .values(
                status="DEAD" if dead else "PENDING",
                attempts=attempts,
                last_status_code=result.status,
                last_error=(result.error or f"HTTP {result.status}")[:300],
                next_attempt_at=next_at,
            )
        )
    return 0


def _mark_error(delivery_id: int, now: datetime, error: str, engine: Engine | None) -> None:
    try:
        with worker_tx(engine) as conn:
            conn.execute(
                update(webhook_deliveries)
                .where(webhook_deliveries.c.id == delivery_id)
                .values(
                    attempts=webhook_deliveries.c.attempts + 1,
                    last_error=f"internal: {error}"[:300],
                    next_attempt_at=now + backoff(1),
                )
            )
    except Exception as exc:
        log.error("could not record webhook error %s: %s", delivery_id, type(exc).__name__)


def replay(conn: Connection, delivery_id: int, now: datetime) -> None:
    """Tenant scoped: re-send a delivered or dead webhook with same event id (receivers dedupe on it)."""
    result = conn.execute(
        update(webhook_deliveries)
        .where(webhook_deliveries.c.id == delivery_id)
        .values(status="PENDING", next_attempt_at=now, created_at=now, last_error=None)
    )
    if result.rowcount != 1:
        raise DeliveryNotFound("Webhook delivery not found")
