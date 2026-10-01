from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from app.modules.intent.domain import Channel, Intent, IntentStatus, Priority, VerificationLevel
from app.modules.intent.rules import (
    EnterpriseIntentRequest,
    PurposeCodePolicy,
    create_enterprise_intent,
)

NOW = datetime(2026, 10, 4, 7, 0, tzinfo=UTC)


@pytest.fixture
def now() -> datetime:
    return NOW


@pytest.fixture
def policy() -> PurposeCodePolicy:
    return PurposeCodePolicy(
        code="MORTGAGE.DOC.CLARIFY",
        approved=True,
        max_priority=Priority.IMPORTANT,
        max_duration_min=10,
        allowed_channels=frozenset({Channel.SDK, Channel.PRECALL_PUSH, Channel.PSTN}),
    )


@pytest.fixture
def request_() -> EnterpriseIntentRequest:
    return EnterpriseIntentRequest(
        tenant_id=uuid4(),
        to_phone="+966500000001",
        agent_id="agt_test",
        purpose_code="MORTGAGE.DOC.CLARIFY",
        priority=Priority.NORMAL,
        expected_duration_min=5,
        valid_from=NOW,
        valid_until=NOW + timedelta(minutes=60),
        channel_preference=(Channel.SDK, Channel.PRECALL_PUSH, Channel.PSTN),
        masked_reference="8291",
    )


@pytest.fixture
def requested(request_: EnterpriseIntentRequest, policy: PurposeCodePolicy) -> Intent:
    return create_enterprise_intent(request_, policy, VerificationLevel.ORG_AGENT_NUMBER, NOW)


def at_status(intent: Intent, status: IntentStatus) -> Intent:
    """Test helper: same intent forced to a status, bypassing state machine. Never use outside tests."""
    return intent.with_changes(status=status)
