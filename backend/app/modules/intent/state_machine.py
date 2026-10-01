"""Intent lifecycle state machine.

Single source of truth for allowed status changes. Every change goes through `transition`, which checks
the table below, checks actor, appends an IntentEvent, and returns a new Intent.
Anything not listed is rejected.

Higher level operations (`respond`, `caller_accept_slot`, `start_call`, `record_outcome`, `expire_if_due`)
map product actions onto transitions and enforce timing rules.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime, timedelta

from .domain import (
    TERMINAL_STATUSES,
    Actor,
    Channel,
    DeclineReason,
    Intent,
    IntentEvent,
    IntentStatus,
    OutcomeCode,
    ResponseAction,
    Slot,
)
from .errors import ActorNotAllowed, IntentExpired, InvalidTransition, RuleViolation

S = IntentStatus
A = Actor

# (from, to) -> actors allowed to make that change
TRANSITIONS: dict[tuple[IntentStatus, IntentStatus], frozenset[Actor]] = {
    (S.DRAFT, S.REQUESTED): frozenset({A.CALLER}),
    (S.DRAFT, S.CANCELLED): frozenset({A.CALLER}),
    (S.REQUESTED, S.DELIVERED): frozenset({A.SYSTEM}),
    (S.REQUESTED, S.EXPIRED): frozenset({A.SYSTEM}),
    (S.REQUESTED, S.CANCELLED): frozenset({A.CALLER}),
    (S.DELIVERED, S.ACCEPTED): frozenset({A.RECEIVER}),
    (S.DELIVERED, S.RESCHEDULED): frozenset({A.RECEIVER}),
    (S.DELIVERED, S.SCHEDULED): frozenset({A.RECEIVER}),
    (S.DELIVERED, S.DECLINED): frozenset({A.RECEIVER}),
    (S.DELIVERED, S.EXPIRED): frozenset({A.SYSTEM}),
    (S.DELIVERED, S.CANCELLED): frozenset({A.CALLER}),
    (S.ACCEPTED, S.IN_PROGRESS): frozenset({A.SYSTEM}),
    (S.ACCEPTED, S.EXPIRED): frozenset({A.SYSTEM}),
    (S.ACCEPTED, S.CANCELLED): frozenset({A.CALLER}),
    (S.RESCHEDULED, S.SCHEDULED): frozenset({A.CALLER}),
    (S.RESCHEDULED, S.DECLINED): frozenset({A.RECEIVER}),
    (S.RESCHEDULED, S.CANCELLED): frozenset({A.CALLER, A.RECEIVER}),
    (S.RESCHEDULED, S.EXPIRED): frozenset({A.SYSTEM}),
    (S.SCHEDULED, S.IN_PROGRESS): frozenset({A.SYSTEM}),
    (S.SCHEDULED, S.RESCHEDULED): frozenset({A.CALLER, A.RECEIVER}),
    (S.SCHEDULED, S.DECLINED): frozenset({A.RECEIVER}),
    (S.SCHEDULED, S.CANCELLED): frozenset({A.CALLER, A.RECEIVER}),
    (S.SCHEDULED, S.EXPIRED): frozenset({A.SYSTEM}),
    (S.IN_PROGRESS, S.COMPLETED): frozenset({A.SYSTEM, A.CALLER, A.RECEIVER}),
    (S.IN_PROGRESS, S.FOLLOW_UP_REQUIRED): frozenset({A.SYSTEM, A.CALLER, A.RECEIVER}),
    (S.FOLLOW_UP_REQUIRED, S.COMPLETED): frozenset({A.SYSTEM, A.CALLER, A.RECEIVER}),
}

# Statuses that lapse when their validity window passes without contact.
EXPIRABLE: frozenset[IntentStatus] = frozenset(
    {S.REQUESTED, S.DELIVERED, S.ACCEPTED, S.RESCHEDULED, S.SCHEDULED}
)

# After a scheduled slot ends, intent stays callable this long before it expires.
SCHEDULE_GRACE = timedelta(minutes=15)
MAX_PROPOSED_SLOTS = 5
LATER_OPTIONS_MIN = frozenset({15, 30, 60, 120})


def allowed_targets(status: IntentStatus) -> set[IntentStatus]:
    return {to for (frm, to) in TRANSITIONS if frm == status}


def transition(
    intent: Intent,
    to: IntentStatus,
    actor: Actor,
    now: datetime,
    reason: str | None = None,
    **changes: object,
) -> Intent:
    """Apply one status change. Raises InvalidTransition or ActorNotAllowed; never mutates input."""
    _require_aware(now)
    key = (intent.status, to)
    actors = TRANSITIONS.get(key)
    if actors is None:
        raise InvalidTransition(f"Cannot move intent from {intent.status} to {to}")
    if actor not in actors:
        raise ActorNotAllowed(f"{actor} may not move intent from {intent.status} to {to}")
    event = IntentEvent(at=now, from_status=intent.status, to_status=to, actor=actor, reason=reason)
    return intent.with_changes(status=to, updated_at=now, timeline=(*intent.timeline, event), **changes)


def is_expired(intent: Intent, now: datetime) -> bool:
    return intent.status in EXPIRABLE and now > intent.valid_until


def expire_if_due(intent: Intent, now: datetime) -> Intent:
    """System sweep: returns EXPIRED intent if its window has passed, otherwise intent unchanged."""
    if is_expired(intent, now):
        return transition(intent, S.EXPIRED, A.SYSTEM, now, reason="validity window passed")
    return intent


def mark_delivered(intent: Intent, now: datetime, channel: Channel) -> Intent:
    _guard_not_expired(intent, now)
    return transition(intent, S.DELIVERED, A.SYSTEM, now, channel_used=channel)


def cancel(intent: Intent, actor: Actor, now: datetime, reason: str | None = None) -> Intent:
    if intent.status in TERMINAL_STATUSES:
        raise InvalidTransition(f"Intent already {intent.status}")
    return transition(intent, S.CANCELLED, actor, now, reason=reason)


def respond(
    intent: Intent,
    action: ResponseAction,
    now: datetime,
    *,
    later_minutes: int | None = None,
    proposed_slots: Sequence[Slot] = (),
    slot: Slot | None = None,
    decline_reason: DeclineReason | None = None,
) -> Intent:
    """Receiver response from incoming intent screen."""
    _require_aware(now)
    _guard_not_expired(intent, now)

    if action is ResponseAction.ACCEPT:
        return transition(intent, S.ACCEPTED, A.RECEIVER, now)

    if action is ResponseAction.LATER:
        if later_minutes not in LATER_OPTIONS_MIN:
            raise RuleViolation(f"later_minutes must be one of {sorted(LATER_OPTIONS_MIN)}")
        start = now + timedelta(minutes=later_minutes)
        chosen = Slot(start=start, end=start + timedelta(minutes=intent.expected_duration_min))
        _check_slot_against_deadline(intent, chosen)
        return transition(
            intent,
            S.SCHEDULED,
            A.RECEIVER,
            now,
            scheduled_slot=chosen,
            valid_until=_extended_validity(intent, chosen),
            proposed_slots=(),
        )

    if action is ResponseAction.PROPOSE:
        _check_proposals(intent, proposed_slots, now)
        return transition(
            intent,
            S.RESCHEDULED,
            A.RECEIVER,
            now,
            proposed_slots=tuple(proposed_slots),
            scheduled_slot=None,
            valid_until=_extended_validity(intent, max(proposed_slots, key=lambda s: s.end)),
        )

    if action is ResponseAction.SCHEDULE:
        if slot is None or slot not in intent.proposed_slots:
            raise RuleViolation("slot must be one of caller's proposed slots")
        if slot.start <= now:
            raise RuleViolation("slot is in the past")
        return transition(
            intent,
            S.SCHEDULED,
            A.RECEIVER,
            now,
            scheduled_slot=slot,
            valid_until=_extended_validity(intent, slot),
            proposed_slots=(),
        )

    if action is ResponseAction.MESSAGE:
        return transition(
            intent,
            S.DECLINED,
            A.RECEIVER,
            now,
            reason="message instead",
            decline_reason=DeclineReason.MESSAGE_INSTEAD,
        )

    if action is ResponseAction.DECLINE:
        if decline_reason is None:
            raise RuleViolation("decline_reason is required")
        return transition(intent, S.DECLINED, A.RECEIVER, now, decline_reason=decline_reason)

    raise RuleViolation(f"Unsupported action {action}")  # pragma: no cover


def caller_accept_slot(intent: Intent, slot: Slot, now: datetime) -> Intent:
    """Caller picks one of receiver's proposed slots."""
    _guard_not_expired(intent, now)
    if slot not in intent.proposed_slots:
        raise RuleViolation("slot must be one of receiver's proposed slots")
    if slot.start <= now:
        raise RuleViolation("slot is in the past")
    return transition(
        intent,
        S.SCHEDULED,
        A.CALLER,
        now,
        scheduled_slot=slot,
        valid_until=_extended_validity(intent, slot),
        proposed_slots=(),
    )


def start_call(intent: Intent, now: datetime) -> Intent:
    """System marks call connected. Scheduled intents may start up to 10 minutes before slot."""
    _guard_not_expired(intent, now)
    if intent.status is S.SCHEDULED and intent.scheduled_slot is not None:
        if now < intent.scheduled_slot.start - timedelta(minutes=10):
            raise RuleViolation("call is more than 10 minutes before scheduled slot")
    return transition(intent, S.IN_PROGRESS, A.SYSTEM, now, attempt_count=intent.attempt_count + 1)


def record_outcome(intent: Intent, code: OutcomeCode, actor: Actor, now: datetime) -> Intent:
    if intent.status is S.FOLLOW_UP_REQUIRED:
        return transition(intent, S.COMPLETED, actor, now, outcome_code=code)
    target = S.FOLLOW_UP_REQUIRED if code is OutcomeCode.FOLLOW_UP_REQUIRED else S.COMPLETED
    return transition(intent, target, actor, now, outcome_code=code)


# helpers


def _require_aware(now: datetime) -> None:
    if now.tzinfo is None:
        raise ValueError("now must be timezone aware")


def _guard_not_expired(intent: Intent, now: datetime) -> None:
    if is_expired(intent, now):
        raise IntentExpired("Intent validity window has passed")


def _extended_validity(intent: Intent, slot: Slot) -> datetime:
    return max(intent.valid_until, slot.end + SCHEDULE_GRACE)


def _check_slot_against_deadline(intent: Intent, slot: Slot) -> None:
    if intent.deadline is not None and slot.end > intent.deadline:
        raise RuleViolation("slot ends after intent deadline")


def _check_proposals(intent: Intent, slots: Sequence[Slot], now: datetime) -> None:
    if not slots:
        raise RuleViolation("at least one proposed slot is required")
    if len(slots) > MAX_PROPOSED_SLOTS:
        raise RuleViolation(f"at most {MAX_PROPOSED_SLOTS} proposed slots")
    if len(set(slots)) != len(slots):
        raise RuleViolation("proposed slots must be distinct")
    for s in slots:
        if s.start <= now:
            raise RuleViolation("proposed slot is in the past")
        _check_slot_against_deadline(intent, s)
