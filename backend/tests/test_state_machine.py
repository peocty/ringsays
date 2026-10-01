from __future__ import annotations

from datetime import datetime, timedelta
from itertools import product

import pytest

from app.modules.intent import state_machine as sm
from app.modules.intent.domain import (
    TERMINAL_STATUSES,
    Actor,
    Channel,
    DeclineReason,
    Intent,
    IntentStatus,
    OutcomeCode,
    ResponseAction,
    Slot,
)
from app.modules.intent.errors import ActorNotAllowed, IntentExpired, InvalidTransition, RuleViolation
from tests.conftest import at_status

S = IntentStatus


# Exhaustive table checks


@pytest.mark.parametrize("frm,to,actor", list(product(S, S, Actor)))
def test_every_pair_and_actor_matches_table(
    requested: Intent, now: datetime, frm: IntentStatus, to: IntentStatus, actor: Actor
) -> None:
    intent = at_status(requested, frm)
    allowed = sm.TRANSITIONS.get((frm, to))
    if allowed is None:
        with pytest.raises(InvalidTransition):
            sm.transition(intent, to, actor, now)
    elif actor not in allowed:
        with pytest.raises(ActorNotAllowed):
            sm.transition(intent, to, actor, now)
    else:
        result = sm.transition(intent, to, actor, now)
        assert result.status is to
        assert result.timeline[-1].from_status is frm
        assert result.timeline[-1].to_status is to
        assert result.timeline[-1].actor is actor


def test_terminal_statuses_have_no_exits() -> None:
    for status in TERMINAL_STATUSES:
        assert sm.allowed_targets(status) == set()


def test_every_non_terminal_status_can_end() -> None:
    """From every live status there is a path to a terminal status."""
    for start in set(S) - TERMINAL_STATUSES:
        seen, frontier = {start}, [start]
        while frontier:
            for nxt in sm.allowed_targets(frontier.pop()):
                if nxt not in seen:
                    seen.add(nxt)
                    frontier.append(nxt)
        assert seen & TERMINAL_STATUSES, start


def test_transition_is_immutable(requested: Intent, now: datetime) -> None:
    after = sm.transition(requested, S.DELIVERED, Actor.SYSTEM, now)
    assert requested.status is S.REQUESTED
    assert after.status is S.DELIVERED
    assert len(after.timeline) == len(requested.timeline) + 1


def test_naive_time_rejected(requested: Intent) -> None:
    with pytest.raises(ValueError):
        sm.transition(requested, S.DELIVERED, Actor.SYSTEM, datetime(2026, 10, 4, 7, 0))


# Delivery and expiry


def test_mark_delivered_records_channel(requested: Intent, now: datetime) -> None:
    out = sm.mark_delivered(requested, now, Channel.SDK)
    assert out.status is S.DELIVERED
    assert out.channel_used is Channel.SDK


def test_expire_if_due_only_after_window(requested: Intent) -> None:
    before = requested.valid_until
    assert sm.expire_if_due(requested, before).status is S.REQUESTED
    expired = sm.expire_if_due(requested, before + timedelta(seconds=1))
    assert expired.status is S.EXPIRED
    assert expired.timeline[-1].actor is Actor.SYSTEM


def test_expire_ignores_in_progress(requested: Intent) -> None:
    live = at_status(requested, S.IN_PROGRESS)
    assert sm.expire_if_due(live, requested.valid_until + timedelta(days=1)).status is S.IN_PROGRESS


def test_respond_after_expiry_is_gone(requested: Intent, now: datetime) -> None:
    delivered = sm.mark_delivered(requested, now, Channel.SDK)
    with pytest.raises(IntentExpired):
        sm.respond(delivered, ResponseAction.ACCEPT, requested.valid_until + timedelta(minutes=1))


# Receiver responses


@pytest.fixture
def delivered(requested: Intent, now: datetime) -> Intent:
    return sm.mark_delivered(requested, now, Channel.SDK)


def test_accept(delivered: Intent, now: datetime) -> None:
    assert sm.respond(delivered, ResponseAction.ACCEPT, now).status is S.ACCEPTED


def test_later_schedules_slot_and_extends_validity(delivered: Intent, now: datetime) -> None:
    out = sm.respond(delivered, ResponseAction.LATER, now, later_minutes=120)
    assert out.status is S.SCHEDULED
    assert out.scheduled_slot is not None
    assert out.scheduled_slot.start == now + timedelta(minutes=120)
    assert out.scheduled_slot.end == now + timedelta(minutes=125)
    assert out.valid_until == out.scheduled_slot.end + sm.SCHEDULE_GRACE


def test_later_rejects_odd_minutes(delivered: Intent, now: datetime) -> None:
    with pytest.raises(RuleViolation):
        sm.respond(delivered, ResponseAction.LATER, now, later_minutes=7)


def test_later_respects_deadline(delivered: Intent, now: datetime) -> None:
    tight = delivered.with_changes(deadline=now + timedelta(minutes=20))
    with pytest.raises(RuleViolation):
        sm.respond(tight, ResponseAction.LATER, now, later_minutes=30)


def _slot(now: datetime, hours: int, minutes: int = 10) -> Slot:
    start = now + timedelta(hours=hours)
    return Slot(start=start, end=start + timedelta(minutes=minutes))


def test_propose_then_caller_accepts(delivered: Intent, now: datetime) -> None:
    a, b = _slot(now, 2), _slot(now, 26)
    proposed = sm.respond(delivered, ResponseAction.PROPOSE, now, proposed_slots=[a, b])
    assert proposed.status is S.RESCHEDULED
    assert proposed.proposed_slots == (a, b)
    assert proposed.valid_until == b.end + sm.SCHEDULE_GRACE
    scheduled = sm.caller_accept_slot(proposed, b, now + timedelta(minutes=5))
    assert scheduled.status is S.SCHEDULED
    assert scheduled.scheduled_slot == b
    assert scheduled.proposed_slots == ()
    assert scheduled.timeline[-1].actor is Actor.CALLER


def test_caller_cannot_pick_unproposed_slot(delivered: Intent, now: datetime) -> None:
    proposed = sm.respond(delivered, ResponseAction.PROPOSE, now, proposed_slots=[_slot(now, 2)])
    with pytest.raises(RuleViolation):
        sm.caller_accept_slot(proposed, _slot(now, 3), now)


@pytest.mark.parametrize("count", [0, 6])
def test_propose_slot_count_limits(delivered: Intent, now: datetime, count: int) -> None:
    slots = [_slot(now, h + 1) for h in range(count)]
    with pytest.raises(RuleViolation):
        sm.respond(delivered, ResponseAction.PROPOSE, now, proposed_slots=slots)


def test_propose_rejects_past_and_duplicate(delivered: Intent, now: datetime) -> None:
    past = Slot(start=now - timedelta(minutes=5), end=now + timedelta(minutes=5))
    with pytest.raises(RuleViolation):
        sm.respond(delivered, ResponseAction.PROPOSE, now, proposed_slots=[past])
    dup = _slot(now, 2)
    with pytest.raises(RuleViolation):
        sm.respond(delivered, ResponseAction.PROPOSE, now, proposed_slots=[dup, dup])


def test_schedule_from_caller_offered_slots(delivered: Intent, now: datetime) -> None:
    offered = _slot(now, 4)
    with_offer = delivered.with_changes(proposed_slots=(offered,))
    out = sm.respond(with_offer, ResponseAction.SCHEDULE, now, slot=offered)
    assert out.status is S.SCHEDULED
    assert out.scheduled_slot == offered
    with pytest.raises(RuleViolation):
        sm.respond(with_offer, ResponseAction.SCHEDULE, now, slot=_slot(now, 5))


def test_message_instead_declines_with_reason(delivered: Intent, now: datetime) -> None:
    out = sm.respond(delivered, ResponseAction.MESSAGE, now)
    assert out.status is S.DECLINED
    assert out.decline_reason is DeclineReason.MESSAGE_INSTEAD


def test_decline_requires_reason(delivered: Intent, now: datetime) -> None:
    with pytest.raises(RuleViolation):
        sm.respond(delivered, ResponseAction.DECLINE, now)
    out = sm.respond(delivered, ResponseAction.DECLINE, now, decline_reason=DeclineReason.WRONG_PERSON)
    assert out.decline_reason is DeclineReason.WRONG_PERSON


def test_receiver_cannot_respond_before_delivery(requested: Intent, now: datetime) -> None:
    with pytest.raises(InvalidTransition):
        sm.respond(requested, ResponseAction.ACCEPT, now)


# Call and outcome


def test_start_call_not_too_early(delivered: Intent, now: datetime) -> None:
    scheduled = sm.respond(delivered, ResponseAction.LATER, now, later_minutes=60)
    with pytest.raises(RuleViolation):
        sm.start_call(scheduled, now + timedelta(minutes=30))
    live = sm.start_call(scheduled, now + timedelta(minutes=55))
    assert live.status is S.IN_PROGRESS
    assert live.attempt_count == 1


def test_outcome_resolved_completes(delivered: Intent, now: datetime) -> None:
    live = sm.start_call(sm.respond(delivered, ResponseAction.ACCEPT, now), now)
    done = sm.record_outcome(live, OutcomeCode.RESOLVED, Actor.CALLER, now)
    assert done.status is S.COMPLETED
    assert done.outcome_code is OutcomeCode.RESOLVED


def test_outcome_follow_up_then_complete(delivered: Intent, now: datetime) -> None:
    live = sm.start_call(sm.respond(delivered, ResponseAction.ACCEPT, now), now)
    fu = sm.record_outcome(live, OutcomeCode.FOLLOW_UP_REQUIRED, Actor.CALLER, now)
    assert fu.status is S.FOLLOW_UP_REQUIRED
    done = sm.record_outcome(fu, OutcomeCode.RESOLVED, Actor.CALLER, now)
    assert done.status is S.COMPLETED


def test_outcome_before_call_rejected(delivered: Intent, now: datetime) -> None:
    with pytest.raises(InvalidTransition):
        sm.record_outcome(delivered, OutcomeCode.RESOLVED, Actor.CALLER, now)


# Cancel


@pytest.mark.parametrize("status", [S.REQUESTED, S.DELIVERED, S.ACCEPTED, S.SCHEDULED, S.RESCHEDULED])
def test_caller_can_cancel_before_contact(requested: Intent, now: datetime, status: IntentStatus) -> None:
    assert sm.cancel(at_status(requested, status), Actor.CALLER, now).status is S.CANCELLED


def test_cannot_cancel_terminal_or_live_call(requested: Intent, now: datetime) -> None:
    for status in [*TERMINAL_STATUSES, S.IN_PROGRESS]:
        with pytest.raises(InvalidTransition):
            sm.cancel(at_status(requested, status), Actor.CALLER, now)


def test_full_happy_path_timeline(requested: Intent, now: datetime) -> None:
    t = now
    i = sm.mark_delivered(requested, t, Channel.SDK)
    i = sm.respond(i, ResponseAction.LATER, t, later_minutes=15)
    i = sm.start_call(i, t + timedelta(minutes=15))
    i = sm.record_outcome(i, OutcomeCode.RESOLVED, Actor.CALLER, t + timedelta(minutes=21))
    assert [e.to_status for e in i.timeline] == [
        S.REQUESTED,
        S.DELIVERED,
        S.SCHEDULED,
        S.IN_PROGRESS,
        S.COMPLETED,
    ]
