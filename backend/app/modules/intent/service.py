"""Intent application service. Every write: load with lock, apply state machine, save, outbox, audit.

All writes happen in one tenant scoped transaction supplied by caller, so state, events, outbox rows and
audit record commit together or not at all.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from uuid import UUID

from sqlalchemy import Connection

from app.core.ids import uuid7
from app.modules.audit import service as audit
from app.modules.context import service as context
from app.modules.enterprise import service as enterprise
from app.modules.webhooks import service as webhooks
from app.platform import outbox, ratelimit

from . import repo
from . import state_machine as sm
from .domain import Actor, Channel, Intent, IntentEvent, OutcomeCode, Slot
from .errors import RuleViolation
from .rules import EnterpriseIntentRequest, create_enterprise_intent
from .schemas import IntentCreateIn


def create(
    conn: Connection, tenant_id: UUID, body: IntentCreateIn, actor: str, now: datetime
) -> tuple[Intent, str | None]:
    """Create intent. Returns (intent, context_token); token only when SDK channel was requested."""
    agent = enterprise.get_agent(conn, body.agent_id)
    if body.department_id is not None and body.department_id != agent.department_id:
        raise RuleViolation("department_id must match the agent's department")
    if body.parent_intent_id is not None:
        # Loaded through tenant scoped connection, so another tenant's intent looks absent.
        try:
            repo.load(conn, body.parent_intent_id)
        except repo.IntentNotFound as exc:
            raise RuleViolation("parent_intent_id does not refer to an intent of this tenant") from exc
    level = enterprise.verification_level(conn, tenant_id, agent)
    policy = enterprise.purpose_policy(conn, body.purpose_code)
    req = EnterpriseIntentRequest(
        tenant_id=tenant_id,
        to_phone=body.to.phone,
        agent_id=agent.agent_id,
        department_id=agent.department_id,
        purpose_code=body.purpose_code,
        priority=body.priority,
        expected_duration_min=body.expected_duration_min,
        valid_from=body.valid_from,
        valid_until=body.valid_until,
        channel_preference=tuple(dict.fromkeys(body.channel_preference)),
        masked_reference=body.masked_reference,
        deadline=body.deadline,
        consent_ref=body.consent_ref,
        parent_intent_id=body.parent_intent_id,
        language=body.language,
        offered_slots=tuple(x.to_domain() for x in body.offered_slots),
    )
    intent = create_enterprise_intent(req, policy, level, now)
    # Counted only once validation passed; caller holds the idempotency lock, so a retried key never counts.
    ratelimit.get_limiter().check_create(tenant_id, body.priority, body.to.phone, now)
    repo.insert_intent(conn, intent)
    outbox.enqueue(
        conn,
        tenant_id,
        outbox.subject_for(tenant_id, "intent", "created"),
        {
            "event_id": str(uuid7()),
            "occurred_at": now.isoformat(),
            "schema_version": 1,
            "tenant_id": str(tenant_id),
            "intent_id": str(intent.intent_id),
            "purpose_code": intent.purpose_code,
            "priority": intent.priority.value,
            "valid_until": intent.valid_until.isoformat(),
        },
    )
    audit.append(
        conn,
        tenant_id=tenant_id,
        actor=actor,
        action="intent.create",
        object_type="intent",
        object_id=str(intent.intent_id),
        at=now,
    )
    token = context.issue(conn, intent, now) if Channel.SDK in intent.preferred_channels else None
    return intent, token


def get(conn: Connection, intent_id: UUID) -> Intent:
    intent, _ = repo.load(conn, intent_id)
    return intent


def cancel(conn: Connection, intent_id: UUID, actor: str, now: datetime) -> Intent:
    return apply(conn, intent_id, lambda i: sm.cancel(i, Actor.CALLER, now), actor, "intent.cancel", now)


def signal_calling(conn: Connection, intent_id: UUID, actor: str, now: datetime) -> Intent:
    """Agent is dialling: intent moves IN_PROGRESS. API layer sends best effort pre call push first."""
    return apply(conn, intent_id, lambda i: sm.start_call(i, now), actor, "intent.calling", now)


def record_outcome(conn: Connection, intent_id: UUID, code: OutcomeCode, actor: str, now: datetime) -> Intent:
    return apply(
        conn, intent_id, lambda i: sm.record_outcome(i, code, Actor.CALLER, now), actor, "intent.outcome", now
    )


def schedule(conn: Connection, intent_id: UUID, slot: Slot, actor: str, now: datetime) -> Intent:
    """Organisation picks one of the times the customer proposed."""
    return apply(
        conn, intent_id, lambda i: sm.caller_accept_slot(i, slot, now), actor, "intent.schedule", now
    )


def mark_delivered(conn: Connection, intent_id: UUID, channel: Channel, now: datetime) -> Intent:
    """Called by delivery module once a channel confirms delivery."""
    return apply(
        conn,
        intent_id,
        lambda i: sm.mark_delivered(i, now, channel),
        "system:delivery",
        "intent.delivered",
        now,
    )


def apply_receiver(
    conn: Connection, intent_id: UUID, fn: Callable[[Intent], Intent], actor: str, now: datetime
) -> Intent:
    """Receiver side changes (client API). `fn` wraps a state_machine.respond call."""
    return apply(conn, intent_id, fn, actor, "intent.respond", now)


def apply(
    conn: Connection, intent_id: UUID, fn: Callable[[Intent], Intent], actor: str, action: str, now: datetime
) -> Intent:
    before, version = repo.load(conn, intent_id, for_update=True)
    after = fn(before)
    if after is before:
        return before
    repo.save(conn, before, after, version)
    for event in after.timeline[len(before.timeline) :]:
        _publish_status_change(conn, after, event, now)
        webhooks.enqueue_for_event(conn, after, event, now)
    if after.is_terminal:
        context.revoke_for_intent(conn, after.intent_id, now)
    audit.append(
        conn,
        tenant_id=after.tenant_id,
        actor=actor,
        action=action,
        object_type="intent",
        object_id=str(after.intent_id),
        at=now,
        reason=f"{before.status.value}->{after.status.value}",
    )
    return after


def _publish_status_change(conn: Connection, i: Intent, e: IntentEvent, now: datetime) -> None:
    outbox.enqueue(
        conn,
        i.tenant_id,
        outbox.subject_for(i.tenant_id, "intent", "status_changed"),
        {
            "event_id": str(uuid7()),
            "occurred_at": e.at.isoformat(),
            "schema_version": 1,
            "tenant_id": str(i.tenant_id) if i.tenant_id else None,
            "intent_id": str(i.intent_id),
            "from_status": e.from_status.value,
            "to_status": e.to_status.value,
            "actor": e.actor.value,
            "reason": e.reason,
        },
    )
