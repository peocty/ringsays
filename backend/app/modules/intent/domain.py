"""Communication Intent domain types.

Pure Python, no framework or database imports. Persistence and API layers map to and from these types.
Enum values must match contracts/openapi/common.yaml exactly; tests/test_contract_parity.py enforces it.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import datetime
from enum import StrEnum
from uuid import UUID


class IntentStatus(StrEnum):
    DRAFT = "DRAFT"
    REQUESTED = "REQUESTED"
    DELIVERED = "DELIVERED"
    ACCEPTED = "ACCEPTED"
    RESCHEDULED = "RESCHEDULED"
    DECLINED = "DECLINED"
    SCHEDULED = "SCHEDULED"
    IN_PROGRESS = "IN_PROGRESS"
    COMPLETED = "COMPLETED"
    FOLLOW_UP_REQUIRED = "FOLLOW_UP_REQUIRED"
    CANCELLED = "CANCELLED"
    EXPIRED = "EXPIRED"


TERMINAL_STATUSES: frozenset[IntentStatus] = frozenset(
    {IntentStatus.COMPLETED, IntentStatus.DECLINED, IntentStatus.CANCELLED, IntentStatus.EXPIRED}
)


class Priority(StrEnum):
    LOW = "LOW"
    NORMAL = "NORMAL"
    IMPORTANT = "IMPORTANT"
    URGENT = "URGENT"

    @property
    def rank(self) -> int:
        return _PRIORITY_RANK[self]


_PRIORITY_RANK = {Priority.LOW: 0, Priority.NORMAL: 1, Priority.IMPORTANT: 2, Priority.URGENT: 3}


class IntentSource(StrEnum):
    DECLARED = "DECLARED"
    PREDICTED = "PREDICTED"
    UNKNOWN = "UNKNOWN"


class VerificationLevel(StrEnum):
    NONE = "NONE"
    PHONE = "PHONE"
    ORG = "ORG"
    ORG_AGENT_NUMBER = "ORG_AGENT_NUMBER"


class Channel(StrEnum):
    VOIP = "VOIP"
    SDK = "SDK"
    PRECALL_PUSH = "PRECALL_PUSH"
    PSTN = "PSTN"
    PLATFORM_CALLER_ID = "PLATFORM_CALLER_ID"


class Actor(StrEnum):
    CALLER = "CALLER"
    RECEIVER = "RECEIVER"
    SYSTEM = "SYSTEM"


class ResponseAction(StrEnum):
    ACCEPT = "ACCEPT"
    LATER = "LATER"
    PROPOSE = "PROPOSE"
    SCHEDULE = "SCHEDULE"
    MESSAGE = "MESSAGE"
    DECLINE = "DECLINE"


class DeclineReason(StrEnum):
    NOT_INTERESTED = "NOT_INTERESTED"
    WRONG_PERSON = "WRONG_PERSON"
    ALREADY_RESOLVED = "ALREADY_RESOLVED"
    NOT_NOW = "NOT_NOW"
    MESSAGE_INSTEAD = "MESSAGE_INSTEAD"
    OTHER = "OTHER"


class OutcomeCode(StrEnum):
    RESOLVED = "RESOLVED"
    FOLLOW_UP_REQUIRED = "FOLLOW_UP_REQUIRED"
    NO_DECISION = "NO_DECISION"
    CALL_BACK = "CALL_BACK"
    DOCUMENT_REQUIRED = "DOCUMENT_REQUIRED"
    TASK_CREATED = "TASK_CREATED"
    MEETING_REQUIRED = "MEETING_REQUIRED"
    ESCALATED = "ESCALATED"
    CANCELLED = "CANCELLED"


@dataclass(frozen=True, slots=True)
class Slot:
    start: datetime
    end: datetime

    def __post_init__(self) -> None:
        if self.start.tzinfo is None or self.end.tzinfo is None:
            raise ValueError("Slot times must be timezone aware")
        if self.end <= self.start:
            raise ValueError("Slot end must be after start")


@dataclass(frozen=True, slots=True)
class IntentEvent:
    at: datetime
    from_status: IntentStatus
    to_status: IntentStatus
    actor: Actor
    reason: str | None = None


@dataclass(frozen=True, slots=True)
class Intent:
    """Aggregate root. Immutable; every change returns a new instance plus an IntentEvent."""

    intent_id: UUID
    tenant_id: UUID | None
    priority: Priority
    expected_duration_min: int
    intent_source: IntentSource
    verification_level: VerificationLevel
    valid_from: datetime
    valid_until: datetime
    status: IntentStatus
    created_at: datetime
    updated_at: datetime
    to_phone: str | None = None
    agent_id: str | None = None
    department_id: UUID | None = None
    purpose_code: str | None = None
    masked_reference: str | None = None
    subject: str | None = None
    deadline: datetime | None = None
    preferred_channels: tuple[Channel, ...] = ()
    channel_used: Channel | None = None
    consent_ref: str | None = None
    parent_intent_id: UUID | None = None
    attempt_count: int = 0
    language: str = "ar"
    proposed_slots: tuple[Slot, ...] = ()
    scheduled_slot: Slot | None = None
    decline_reason: DeclineReason | None = None
    outcome_code: OutcomeCode | None = None
    timeline: tuple[IntentEvent, ...] = field(default=())

    @property
    def is_terminal(self) -> bool:
        return self.status in TERMINAL_STATUSES

    def with_changes(self, **changes: object) -> Intent:
        return replace(self, **changes)  # type: ignore[arg-type]
