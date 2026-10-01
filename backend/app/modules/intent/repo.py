"""Intent persistence. Maps immutable domain Intent to rows; optimistic locking on `version`."""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import Connection, Row, insert, select, update

from app.core.phone import phone_hash
from app.core.tables import intent_events, intents

from .domain import (
    Actor,
    Channel,
    DeclineReason,
    Intent,
    IntentEvent,
    IntentSource,
    IntentStatus,
    OutcomeCode,
    Priority,
    Slot,
    VerificationLevel,
)
from .errors import IntentError


class ConcurrentModification(IntentError):
    code = "concurrent_modification"
    http_status = 409


class IntentNotFound(IntentError):
    code = "not_found"
    http_status = 404


def _slot_json(s: Slot) -> dict[str, str]:
    return {"start": s.start.isoformat(), "end": s.end.isoformat()}


def _slot_from(d: dict[str, str]) -> Slot:
    return Slot(start=datetime.fromisoformat(d["start"]), end=datetime.fromisoformat(d["end"]))


def _row_values(i: Intent) -> dict[str, Any]:
    if i.to_phone is None:
        raise ValueError("intent must have a recipient phone to persist")
    return {
        "tenant_id": i.tenant_id,
        "agent_id": i.agent_id,
        "department_id": i.department_id,
        "to_phone": i.to_phone,
        "purpose_code": i.purpose_code,
        "masked_reference": i.masked_reference,
        "subject": i.subject,
        "priority": i.priority.value,
        "expected_duration_min": i.expected_duration_min,
        "intent_source": i.intent_source.value,
        "verification_level": i.verification_level.value,
        "valid_from": i.valid_from,
        "valid_until": i.valid_until,
        "deadline": i.deadline,
        "preferred_channels": [c.value for c in i.preferred_channels],
        "channel_used": i.channel_used.value if i.channel_used else None,
        "consent_ref": i.consent_ref,
        "parent_intent_id": i.parent_intent_id,
        "attempt_count": i.attempt_count,
        "language": i.language,
        "proposed_slots": [_slot_json(s) for s in i.proposed_slots],
        "scheduled_slot": _slot_json(i.scheduled_slot) if i.scheduled_slot else None,
        "decline_reason": i.decline_reason.value if i.decline_reason else None,
        "outcome_code": i.outcome_code.value if i.outcome_code else None,
        "status": i.status.value,
        "updated_at": i.updated_at,
    }


def _from_row(r: Row[Any], events: list[IntentEvent]) -> Intent:
    return Intent(
        intent_id=r.id,
        tenant_id=r.tenant_id,
        to_phone=r.to_phone,
        agent_id=r.agent_id,
        department_id=r.department_id,
        purpose_code=r.purpose_code,
        masked_reference=r.masked_reference,
        subject=r.subject,
        priority=Priority(r.priority),
        expected_duration_min=r.expected_duration_min,
        intent_source=IntentSource(r.intent_source),
        verification_level=VerificationLevel(r.verification_level),
        valid_from=r.valid_from,
        valid_until=r.valid_until,
        deadline=r.deadline,
        preferred_channels=tuple(Channel(c) for c in r.preferred_channels),
        channel_used=Channel(r.channel_used) if r.channel_used else None,
        consent_ref=r.consent_ref,
        parent_intent_id=r.parent_intent_id,
        attempt_count=r.attempt_count,
        language=r.language,
        proposed_slots=tuple(_slot_from(s) for s in r.proposed_slots),
        scheduled_slot=_slot_from(r.scheduled_slot) if r.scheduled_slot else None,
        decline_reason=DeclineReason(r.decline_reason) if r.decline_reason else None,
        outcome_code=OutcomeCode(r.outcome_code) if r.outcome_code else None,
        status=IntentStatus(r.status),
        created_at=r.created_at,
        updated_at=r.updated_at,
        timeline=tuple(events),
    )


def insert_intent(conn: Connection, i: Intent) -> None:
    conn.execute(
        insert(intents).values(
            id=i.intent_id,
            created_at=i.created_at,
            version=1,
            delivery_next_check_at=i.valid_from,
            to_phone_hash=phone_hash(i.to_phone) if i.to_phone else None,
            **_row_values(i),
        )
    )
    _insert_events(conn, i, i.timeline)


def load(conn: Connection, intent_id: UUID, *, for_update: bool = False) -> tuple[Intent, int]:
    q = select(intents).where(intents.c.id == intent_id)
    if for_update:
        q = q.with_for_update()
    row = conn.execute(q).one_or_none()
    if row is None:
        raise IntentNotFound("Intent not found")
    ev_rows = conn.execute(
        select(intent_events).where(intent_events.c.intent_id == intent_id).order_by(intent_events.c.id)
    ).all()
    events = [
        IntentEvent(
            at=e.at,
            from_status=IntentStatus(e.from_status),
            to_status=IntentStatus(e.to_status),
            actor=Actor(e.actor),
            reason=e.reason,
        )
        for e in ev_rows
    ]
    return _from_row(row, events), row.version


def save(conn: Connection, before: Intent, after: Intent, version: int) -> int:
    """Persist `after`, appending only events added since `before`. Returns new version."""
    result = conn.execute(
        update(intents)
        .where(intents.c.id == after.intent_id, intents.c.version == version)
        .values(version=version + 1, **_row_values(after))
    )
    if result.rowcount != 1:
        raise ConcurrentModification("Intent was changed by another request; retry")
    _insert_events(conn, after, after.timeline[len(before.timeline) :])
    return version + 1


def _insert_events(conn: Connection, i: Intent, events: tuple[IntentEvent, ...]) -> None:
    if not events:
        return
    conn.execute(
        insert(intent_events),
        [
            {
                "intent_id": i.intent_id,
                "tenant_id": i.tenant_id,
                "at": e.at,
                "from_status": e.from_status.value,
                "to_status": e.to_status.value,
                "actor": e.actor.value,
                "reason": e.reason,
            }
            for e in events
        ],
    )
