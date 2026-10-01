"""Creation rules for enterprise intents.

Tenants may only send approved purpose codes, inside caps set per code. Verification level is computed by
enterprise module from tenant, agent and calling number status and passed in; callers never supply it.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from uuid import UUID

from app.core.ids import uuid7

from .domain import (
    Actor,
    Channel,
    Intent,
    IntentEvent,
    IntentSource,
    IntentStatus,
    Priority,
    Slot,
    VerificationLevel,
)
from .errors import RuleViolation

MASKED_REFERENCE = re.compile(r"^[A-Za-z0-9]{3,4}$")
MAX_VALIDITY_WINDOW = timedelta(days=7)
MIN_VALIDITY_WINDOW = timedelta(minutes=5)


@dataclass(frozen=True, slots=True)
class PurposeCodePolicy:
    code: str
    approved: bool
    max_priority: Priority
    max_duration_min: int
    allowed_channels: frozenset[Channel]


@dataclass(frozen=True, slots=True)
class EnterpriseIntentRequest:
    tenant_id: UUID
    to_phone: str
    agent_id: str
    purpose_code: str
    priority: Priority
    expected_duration_min: int
    valid_from: datetime
    valid_until: datetime
    channel_preference: tuple[Channel, ...]
    masked_reference: str | None = None
    deadline: datetime | None = None
    consent_ref: str | None = None
    parent_intent_id: UUID | None = None
    language: str = "ar"
    offered_slots: tuple[Slot, ...] = ()
    department_id: UUID | None = None


def validate_enterprise_request(
    req: EnterpriseIntentRequest, policy: PurposeCodePolicy, now: datetime
) -> None:
    """Raise RuleViolation listing first broken rule."""
    if now.tzinfo is None or req.valid_from.tzinfo is None or req.valid_until.tzinfo is None:
        raise ValueError("timestamps must be timezone aware")
    if policy.code != req.purpose_code:
        raise ValueError("policy does not match requested purpose code")
    if not policy.approved:
        raise RuleViolation(f"purpose code {req.purpose_code} is not approved")
    if req.priority.rank > policy.max_priority.rank:
        raise RuleViolation(
            f"priority {req.priority} exceeds cap {policy.max_priority} for {req.purpose_code}"
        )
    if not 1 <= req.expected_duration_min <= policy.max_duration_min:
        raise RuleViolation(
            f"expected_duration_min must be 1 to {policy.max_duration_min} for {req.purpose_code}"
        )
    if not req.channel_preference:
        raise RuleViolation("channel_preference must not be empty")
    disallowed = set(req.channel_preference) - policy.allowed_channels
    if disallowed:
        raise RuleViolation(f"channels not allowed for this purpose code: {sorted(disallowed)}")
    if req.masked_reference is not None and not MASKED_REFERENCE.fullmatch(req.masked_reference):
        raise RuleViolation("masked_reference must be last 3 or 4 characters only")
    window = req.valid_until - req.valid_from
    if window < MIN_VALIDITY_WINDOW:
        raise RuleViolation("validity window must be at least 5 minutes")
    if window > MAX_VALIDITY_WINDOW:
        raise RuleViolation("validity window must not exceed 7 days")
    if req.valid_until <= now:
        raise RuleViolation("valid_until is in the past")
    if req.deadline is not None and req.deadline < req.valid_from:
        raise RuleViolation("deadline is before valid_from")
    _check_offered_slots(req, now)


def create_enterprise_intent(
    req: EnterpriseIntentRequest,
    policy: PurposeCodePolicy,
    verification_level: VerificationLevel,
    now: datetime,
) -> Intent:
    """Validate and build a REQUESTED intent. Persisting and outbox publishing happen in service layer."""
    validate_enterprise_request(req, policy, now)
    event = IntentEvent(
        at=now, from_status=IntentStatus.DRAFT, to_status=IntentStatus.REQUESTED, actor=Actor.CALLER
    )
    return Intent(
        intent_id=uuid7(),
        tenant_id=req.tenant_id,
        to_phone=req.to_phone,
        agent_id=req.agent_id,
        department_id=req.department_id,
        purpose_code=req.purpose_code,
        masked_reference=req.masked_reference,
        priority=req.priority,
        expected_duration_min=req.expected_duration_min,
        intent_source=IntentSource.DECLARED,
        verification_level=verification_level,
        valid_from=req.valid_from,
        valid_until=req.valid_until,
        deadline=req.deadline,
        preferred_channels=req.channel_preference,
        consent_ref=req.consent_ref,
        parent_intent_id=req.parent_intent_id,
        language=req.language,
        proposed_slots=req.offered_slots,
        status=IntentStatus.REQUESTED,
        created_at=now,
        updated_at=now,
        timeline=(event,),
    )


def _check_offered_slots(req: EnterpriseIntentRequest, now: datetime) -> None:
    slots: Sequence[Slot] = req.offered_slots
    if len(slots) > 5:
        raise RuleViolation("at most 5 offered slots")
    for s in slots:
        if s.start <= now:
            raise RuleViolation("offered slot is in the past")
        if req.deadline is not None and s.end > req.deadline:
            raise RuleViolation("offered slot ends after deadline")
