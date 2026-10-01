from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta

import pytest

from app.modules.intent.domain import Channel, IntentSource, IntentStatus, Priority, Slot, VerificationLevel
from app.modules.intent.errors import RuleViolation
from app.modules.intent.rules import (
    EnterpriseIntentRequest,
    PurposeCodePolicy,
    create_enterprise_intent,
    validate_enterprise_request,
)


def test_creates_requested_declared_intent(
    request_: EnterpriseIntentRequest, policy: PurposeCodePolicy, now: datetime
) -> None:
    intent = create_enterprise_intent(request_, policy, VerificationLevel.ORG_AGENT_NUMBER, now)
    assert intent.status is IntentStatus.REQUESTED
    assert intent.intent_source is IntentSource.DECLARED
    assert intent.verification_level is VerificationLevel.ORG_AGENT_NUMBER
    assert intent.intent_id.version == 7
    assert intent.timeline[0].from_status is IntentStatus.DRAFT


@pytest.mark.parametrize(
    "change,message",
    [
        ({"priority": Priority.URGENT}, "exceeds cap"),
        ({"expected_duration_min": 11}, "expected_duration_min"),
        ({"expected_duration_min": 0}, "expected_duration_min"),
        ({"channel_preference": (Channel.VOIP,)}, "channels not allowed"),
        ({"channel_preference": ()}, "must not be empty"),
        ({"masked_reference": "1234567890"}, "masked_reference"),
        ({"masked_reference": "82-1"}, "masked_reference"),
    ],
)
def test_rejects_rule_breaks(
    request_: EnterpriseIntentRequest,
    policy: PurposeCodePolicy,
    now: datetime,
    change: dict[str, object],
    message: str,
) -> None:
    with pytest.raises(RuleViolation, match=message):
        validate_enterprise_request(replace(request_, **change), policy, now)  # type: ignore[arg-type]


def test_rejects_unapproved_code(
    request_: EnterpriseIntentRequest, policy: PurposeCodePolicy, now: datetime
) -> None:
    with pytest.raises(RuleViolation, match="not approved"):
        validate_enterprise_request(request_, replace(policy, approved=False), now)


@pytest.mark.parametrize(
    "start,end,message",
    [
        (timedelta(minutes=10), timedelta(minutes=12), "at least 5 minutes"),
        (timedelta(minutes=10), timedelta(days=8), "exceed 7 days"),
        (timedelta(minutes=-60), timedelta(minutes=-1), "in the past"),
    ],
)
def test_validity_window(
    request_: EnterpriseIntentRequest,
    policy: PurposeCodePolicy,
    now: datetime,
    start: timedelta,
    end: timedelta,
    message: str,
) -> None:
    req = replace(request_, valid_from=now + start, valid_until=now + end)
    with pytest.raises(RuleViolation, match=message):
        validate_enterprise_request(req, policy, now)


def test_offered_slots_checked(
    request_: EnterpriseIntentRequest, policy: PurposeCodePolicy, now: datetime
) -> None:
    past = Slot(start=now - timedelta(hours=1), end=now - timedelta(minutes=50))
    with pytest.raises(RuleViolation, match="offered slot"):
        validate_enterprise_request(replace(request_, offered_slots=(past,)), policy, now)


def test_policy_mismatch_is_programming_error(
    request_: EnterpriseIntentRequest, policy: PurposeCodePolicy, now: datetime
) -> None:
    with pytest.raises(ValueError):
        validate_enterprise_request(request_, replace(policy, code="OTHER.CODE"), now)
