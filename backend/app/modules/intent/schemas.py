"""API request and response models. Field names and constraints follow contracts/openapi/enterprise.yaml."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

from .domain import Channel, Intent, OutcomeCode, Priority, Slot

E164 = Annotated[str, StringConstraints(pattern=r"^\+[1-9]\d{6,14}$")]
PurposeCodeStr = Annotated[str, StringConstraints(pattern=r"^[A-Z0-9]+(\.[A-Z0-9]+){1,4}$")]
MaskedRef = Annotated[str, StringConstraints(pattern=r"^[A-Za-z0-9]{3,4}$")]


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Recipient(Strict):
    phone: E164


class SlotIn(Strict):
    start: datetime
    end: datetime

    @model_validator(mode="after")
    def _ordered(self) -> SlotIn:
        if self.start.tzinfo is None or self.end.tzinfo is None:
            raise ValueError("slot times need a time zone")
        if self.end <= self.start:
            raise ValueError("slot end must be after start")
        return self

    def to_domain(self) -> Slot:
        return Slot(start=self.start, end=self.end)


class ScheduleIn(Strict):
    slot: SlotIn


class IntentCreateIn(Strict):
    to: Recipient
    agent_id: Annotated[str, StringConstraints(max_length=64)]
    department_id: UUID | None = None
    purpose_code: PurposeCodeStr
    masked_reference: MaskedRef | None = None
    priority: Priority
    expected_duration_min: Annotated[int, Field(ge=1, le=120)]
    deadline: datetime | None = None
    valid_from: datetime
    valid_until: datetime
    channel_preference: Annotated[list[Channel], Field(min_length=1)] = [
        Channel.SDK,
        Channel.PRECALL_PUSH,
        Channel.PSTN,
    ]
    consent_ref: Annotated[str, StringConstraints(max_length=128)] | None = None
    parent_intent_id: UUID | None = None
    language: Literal["en", "ar"] = "ar"
    offered_slots: Annotated[list[SlotIn], Field(max_length=5)] = []


class OutcomeIn(Strict):
    code: OutcomeCode
    follow_up_at: datetime | None = None


class LocalisedText(Strict):
    en: Annotated[str, StringConstraints(min_length=1, max_length=160)]
    ar: Annotated[str, StringConstraints(min_length=1, max_length=160)]


class PurposeCodeIn(Strict):
    code: PurposeCodeStr
    display_text: LocalisedText
    max_priority: Priority
    max_duration_min: Annotated[int, Field(ge=1, le=120)]
    allowed_channels: Annotated[list[Channel], Field(min_length=1)]


def _slot(s: Any) -> dict[str, str] | None:
    return None if s is None else {"start": s.start.isoformat(), "end": s.end.isoformat()}


def intent_out(i: Intent) -> dict[str, Any]:
    """Serialise to contract `Intent` schema. Recipient phone is never echoed back in full."""
    return {
        "intent_id": str(i.intent_id),
        "tenant_id": str(i.tenant_id) if i.tenant_id else None,
        "from_party": {
            "kind": "TENANT_AGENT",
            "tenant_id": str(i.tenant_id) if i.tenant_id else None,
            "agent_id": i.agent_id,
            **({"department_id": str(i.department_id)} if i.department_id else {}),
        },
        "to_party": {"kind": "PHONE"},
        "on_behalf_of": None,
        "purpose_code": i.purpose_code,
        "display_text": None,
        "subject": i.subject,
        "masked_reference": i.masked_reference,
        "priority": i.priority.value,
        "expected_duration_min": i.expected_duration_min,
        "deadline": i.deadline.isoformat() if i.deadline else None,
        "preferred_channels": [c.value for c in i.preferred_channels],
        "channel_used": i.channel_used.value if i.channel_used else None,
        "intent_source": i.intent_source.value,
        "verification_level": i.verification_level.value,
        "consent_ref": i.consent_ref,
        "parent_intent_id": str(i.parent_intent_id) if i.parent_intent_id else None,
        "attempt_count": i.attempt_count,
        "language": i.language,
        "valid_from": i.valid_from.isoformat(),
        "valid_until": i.valid_until.isoformat(),
        "status": i.status.value,
        "scheduled_slot": _slot(i.scheduled_slot),
        "proposed_slots": [_slot(x) for x in i.proposed_slots],
        "created_at": i.created_at.isoformat(),
        "updated_at": i.updated_at.isoformat(),
        "timeline": [
            {
                "at": e.at.isoformat(),
                "from_status": e.from_status.value,
                "to_status": e.to_status.value,
                "actor": e.actor.value,
            }
            for e in i.timeline
        ],
    }
