"""Delivery: walks each intent's fallback ladder (foundation doc section 13) once its window opens.

Ladder, in the order the tenant listed channels:
- SDK: token issued to tenant at create; delivered once tenant app resolves token on a device and the
  window is open. If nothing resolves within SDK_GRACE, ladder moves on.
- PRECALL_PUSH: push to recipient's RingSays devices (if recipient has RingSays and rules allow).
- PSTN: final fallback. Intent marked DELIVERED with channel PSTN, meaning "plain call" (ADR 0008).
- VOIP, PLATFORM_CALLER_ID: not available until MVP 2; recorded as SKIPPED.

Receiver rules can HOLD delivery until a window opens. A BLOCK skips app channels only; RingSays cannot
and does not stop an ordinary phone call. Every step is recorded in intent.delivery_attempts.

Robustness rules (stage 3 review):
- Worker picks intents by `delivery_next_check_at`, so waiting or exhausted intents never crowd out due ones.
- Each intent is handled in its own transactions; an error on one is recorded and backed off, never
  stopping delivery for others.
- Pushes are sent outside database transactions: decide and lease (commit), send, then record result.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Literal
from uuid import UUID

from sqlalchemy import Connection, Engine, insert, select, update

from app.core.db import tenant_tx, worker_tx
from app.core.tables import context_tokens, delivery_attempts, intents
from app.modules.context import service as context
from app.modules.intent import repo
from app.modules.intent import service as intent_service
from app.modules.intent.domain import Channel, Intent, IntentStatus
from app.modules.preference.engine import Decision, IncomingContext, evaluate

from .adapters import PushSender, PushTarget, Recipient, RecipientDirectory

log = logging.getLogger(__name__)

SDK_GRACE = timedelta(minutes=2)
PUSH_LEASE = timedelta(seconds=60)
ERROR_BACKOFF = timedelta(minutes=5)
DEFAULT_CATEGORY = "BANK"  # BFSI launch tenants; tenant sector field comes with admin API


@dataclass
class DeliveryStats:
    delivered: int = 0
    held: int = 0
    waiting_sdk: int = 0
    errors: int = 0


@dataclass(frozen=True, slots=True)
class _Plan:
    kind: Literal["delivered", "held", "waiting_sdk", "push", "exhausted", "skipped"]
    next_check: datetime | None = None
    targets: tuple[PushTarget, ...] = ()
    attempt_id: int | None = None
    recipient: Recipient | None = None


def _record(
    conn: Connection,
    i: Intent,
    channel: Channel,
    outcome: str,
    now: datetime,
    reason: str | None = None,
    hold_until: datetime | None = None,
) -> int:
    return int(
        conn.execute(
            insert(delivery_attempts)
            .values(
                tenant_id=i.tenant_id,
                intent_id=i.intent_id,
                channel=channel.value,
                outcome=outcome,
                reason=reason,
                hold_until=hold_until,
                at=now,
            )
            .returning(delivery_attempts.c.id)
        ).scalar_one()
    )


def _attempts(conn: Connection, intent_id: UUID) -> list[tuple[str, str, datetime]]:
    rows = conn.execute(
        select(delivery_attempts.c.channel, delivery_attempts.c.outcome, delivery_attempts.c.at)
        .where(delivery_attempts.c.intent_id == intent_id)
        .order_by(delivery_attempts.c.id)
    ).all()
    return [(r.channel, r.outcome, r.at) for r in rows]


def _set_next_check(conn: Connection, intent_id: UUID, at: datetime) -> None:
    conn.execute(update(intents).where(intents.c.id == intent_id).values(delivery_next_check_at=at))


def _token_resolved(conn: Connection, intent_id: UUID) -> bool:
    return (
        conn.execute(
            select(context_tokens.c.token_hash).where(
                context_tokens.c.intent_id == intent_id,
                context_tokens.c.resolve_count > 0,
                context_tokens.c.revoked_at.is_(None),
            )
        ).first()
        is not None
    )


def _context(intent: Intent) -> IncomingContext:
    return IncomingContext(
        DEFAULT_CATEGORY, intent.verification_level, intent.priority, intent.expected_duration_min
    )


def deliver_due(
    now: datetime,
    directory: RecipientDirectory,
    push: PushSender,
    *,
    batch: int = 200,
    engine: Engine | None = None,
) -> DeliveryStats:
    """Worker job."""
    with worker_tx(engine) as conn:
        ids = (
            conn.execute(
                select(intents.c.id)
                .where(
                    intents.c.status == IntentStatus.REQUESTED.value,
                    intents.c.delivery_next_check_at <= now,
                    intents.c.valid_from <= now,
                    intents.c.valid_until > now,
                )
                .order_by(intents.c.delivery_next_check_at)
                .limit(batch)
            )
            .scalars()
            .all()
        )
    stats = DeliveryStats()
    for intent_id in ids:
        try:
            result = _deliver_one(intent_id, now, directory, push, engine)
        except Exception as exc:
            stats.errors += 1
            log.error("delivery failed for intent %s: %s", intent_id, type(exc).__name__)
            _record_error(intent_id, now, type(exc).__name__, engine)
            continue
        if result == "delivered":
            stats.delivered += 1
        elif result == "held":
            stats.held += 1
        elif result == "waiting_sdk":
            stats.waiting_sdk += 1
    return stats


def _record_error(intent_id: UUID, now: datetime, error: str, engine: Engine | None) -> None:
    try:
        with worker_tx(engine) as conn:
            intent, _ = repo.load(conn, intent_id)
            _record(conn, intent, intent.preferred_channels[0], "ERROR", now, error[:200])
            _set_next_check(conn, intent_id, now + ERROR_BACKOFF)
    except Exception as exc:
        log.error("could not record delivery error for %s: %s", intent_id, type(exc).__name__)


def _deliver_one(
    intent_id: UUID, now: datetime, directory: RecipientDirectory, push: PushSender, engine: Engine | None
) -> str:
    with worker_tx(engine) as conn:
        plan = _plan(conn, intent_id, now, directory)
        if plan.next_check is not None:
            _set_next_check(conn, intent_id, plan.next_check)
    if plan.kind == "delivered" and plan.recipient is not None:
        _note_contact(directory, plan.recipient, intent_id, now, engine)
    if plan.kind != "push":
        return plan.kind
    # Send outside any transaction, then record result.
    results = [push.send(t, "INTENT", intent_id) for t in plan.targets]
    ok = sum(r.ok for r in results)
    with worker_tx(engine) as conn:
        intent, _ = repo.load(conn, intent_id, for_update=True)
        conn.execute(
            update(delivery_attempts)
            .where(delivery_attempts.c.id == plan.attempt_id)
            .values(
                outcome="SENT" if ok else "FAILED",
                reason=f"{ok} device(s)" if ok else "all devices rejected push",
            )
        )
        if ok and intent.status is IntentStatus.REQUESTED:
            intent_service.mark_delivered(conn, intent_id, Channel.PRECALL_PUSH, now)
            delivered = True
        else:
            delivered = False
            _set_next_check(conn, intent_id, now)  # continue ladder on next tick
    if delivered and plan.recipient is not None:
        _note_contact(directory, plan.recipient, intent_id, now, engine)
    return "delivered" if delivered else "push_failed"


def _note_contact(
    directory: RecipientDirectory, recipient: Recipient, intent_id: UUID, now: datetime, engine: Engine | None
) -> None:
    """Record the organisation in the user's consent ledger after any delivery that reached their device,
    so it can be withdrawn from the RingSays app. Best effort: never undoes the delivery."""
    try:
        with worker_tx(engine) as conn:
            intent, _ = repo.load(conn, intent_id)
        if intent.tenant_id is not None:
            directory.note_contact(recipient, intent.tenant_id, intent.consent_ref, now)
    except Exception as exc:
        log.error("could not record contact for %s: %s", intent_id, type(exc).__name__)


def _plan(conn: Connection, intent_id: UUID, now: datetime, directory: RecipientDirectory) -> _Plan:
    intent, _ = repo.load(conn, intent_id, for_update=True)
    if intent.status is not IntentStatus.REQUESTED or intent.to_phone is None:
        return _Plan("skipped")
    recipient = directory.lookup(intent.to_phone, intent.tenant_id)
    decision = Decision.ALLOW
    if recipient is not None and recipient.blocked:
        decision = Decision.BLOCK
    elif recipient is not None:
        outcome = evaluate(recipient.preferences, _context(intent), now)
        decision = outcome.decision
        if decision is Decision.HOLD_UNTIL_WINDOW and outcome.hold_until is not None:
            if outcome.hold_until < intent.valid_until:
                _record(
                    conn,
                    intent,
                    intent.preferred_channels[0],
                    "HELD",
                    now,
                    outcome.reason,
                    outcome.hold_until,
                )
                return _Plan("held", next_check=outcome.hold_until)
            decision = Decision.BLOCK  # window opens after intent expires: do not wake receiver now

    previous = _attempts(conn, intent.intent_id)
    done = {(c, o) for c, o, _ in previous}
    for channel in intent.preferred_channels:
        if channel is Channel.SDK:
            if _token_resolved(conn, intent.intent_id):
                _record(conn, intent, channel, "SENT", now, "resolved on device")
                intent_service.mark_delivered(conn, intent.intent_id, Channel.SDK, now)
                return _Plan("delivered", recipient=recipient)
            sent = [a for a in previous if a[0] == Channel.SDK.value and a[1] == "SENT"]
            if not sent:
                _record(conn, intent, channel, "SENT", now, "token with tenant; awaiting resolve")
                return _Plan("waiting_sdk", next_check=now + SDK_GRACE)
            if now - sent[0][2] < SDK_GRACE:
                return _Plan("waiting_sdk", next_check=sent[0][2] + SDK_GRACE)
            if (Channel.SDK.value, "FAILED") not in done:
                _record(conn, intent, channel, "FAILED", now, "no resolve within grace period")
            continue
        if channel is Channel.PRECALL_PUSH:
            if any(c == channel.value and o not in ("HELD", "ERROR") for c, o in done):
                continue  # already tried; a SENDING row past its lease counts as tried, never push twice
            targets = _push_targets(conn, intent, recipient, decision, now)
            if targets:
                attempt_id = _record(conn, intent, channel, "SENDING", now, "push in flight")
                return _Plan(
                    "push",
                    next_check=now + PUSH_LEASE,
                    targets=targets,
                    attempt_id=attempt_id,
                    recipient=recipient,
                )
            continue
        if channel is Channel.PSTN:
            _record(conn, intent, channel, "SENT", now, "fallback to plain call")
            intent_service.mark_delivered(conn, intent.intent_id, Channel.PSTN, now)
            return _Plan("delivered")
        if not any(c == channel.value and o == "SKIPPED" for c, o in done):
            _record(conn, intent, channel, "SKIPPED", now, "channel available from MVP 2")
    return _Plan("exhausted", next_check=intent.valid_until)


def _push_targets(
    conn: Connection, intent: Intent, recipient: Recipient | None, decision: Decision, now: datetime
) -> tuple[PushTarget, ...]:
    if recipient is None or not recipient.devices:
        _record(conn, intent, Channel.PRECALL_PUSH, "SKIPPED", now, "recipient has no RingSays device")
        return ()
    if decision is Decision.BLOCK:
        _record(conn, intent, Channel.PRECALL_PUSH, "SKIPPED", now, "receiver rules")
        return ()
    return recipient.devices


def deliver_via_sdk(conn_anonymous: Connection, token: str, device_id: UUID, now: datetime) -> Intent:
    """Tenant app (through SDK) resolves token on a device.

    Resolve is pulled by the customer inside the tenant's own app, so receiver quiet rules (which govern
    interruptions) do not apply. Before the window opens the resolve only binds the device; the worker
    delivers when the window opens.
    """
    tenant_id, intent_id = context.resolve(conn_anonymous, token, device_id, now)
    delivered = False
    with tenant_tx(tenant_id) as conn:
        intent, _ = repo.load(conn, intent_id, for_update=True)
        if intent.status is IntentStatus.REQUESTED and intent.valid_from <= now:
            _record(conn, intent, Channel.SDK, "SENT", now, "resolved on device")
            intent = intent_service.mark_delivered(conn, intent_id, Channel.SDK, now)
            delivered = True
    if delivered and intent.to_phone is not None:
        from .adapters import get_directory

        directory = get_directory()
        recipient = directory.lookup(intent.to_phone, tenant_id)
        if recipient is not None:
            try:
                directory.note_contact(recipient, tenant_id, intent.consent_ref, now)
            except Exception as exc:
                log.error("could not record contact for %s: %s", intent_id, type(exc).__name__)
    return intent


def precall_push_after_commit(
    tenant_id: UUID, intent_id: UUID, directory: RecipientDirectory, push: PushSender, now: datetime
) -> bool:
    """Best effort 'calling now' push, sent only after the calling transition has committed.

    Skipped for PSTN fallback intents, when recipient has no device, or when receiver rules would not
    allow an interruption now (URGENT from verified organisations still passes quiet hours).
    """
    with tenant_tx(tenant_id) as conn:
        intent, _ = repo.load(conn, intent_id)
    if intent.status is not IntentStatus.IN_PROGRESS or intent.to_phone is None:
        return False
    if intent.channel_used not in (Channel.SDK, Channel.PRECALL_PUSH):
        return False
    recipient = directory.lookup(intent.to_phone, intent.tenant_id)
    if recipient is None or not recipient.devices:
        return False
    decision = (
        Decision.BLOCK
        if recipient.blocked
        else evaluate(recipient.preferences, _context(intent), now).decision
    )
    if decision is not Decision.ALLOW:
        with tenant_tx(tenant_id) as conn:
            _record(conn, intent, Channel.PRECALL_PUSH, "SKIPPED", now, "calling now: receiver rules")
        return False
    ok = any(push.send(d, "PRECALL", intent_id).ok for d in recipient.devices)
    with tenant_tx(tenant_id) as conn:
        _record(conn, intent, Channel.PRECALL_PUSH, "SENT" if ok else "FAILED", now, "calling now")
    return ok
