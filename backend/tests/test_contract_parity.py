"""Guards that backend enums and seed data match published contracts. Fails CI on drift."""

from __future__ import annotations

from enum import StrEnum
from pathlib import Path

import pytest
import yaml

from app.modules.intent import domain
from app.modules.intent.domain import Channel, Priority
from app.modules.intent.rules import MASKED_REFERENCE

ROOT = Path(__file__).resolve().parents[2]
COMMON = yaml.safe_load((ROOT / "contracts/openapi/common.yaml").read_text())["components"]["schemas"]
SEED = yaml.safe_load((ROOT / "contracts/purpose-codes/seed.yaml").read_text())


@pytest.mark.parametrize(
    "schema,enum_cls",
    [
        ("IntentStatus", domain.IntentStatus),
        ("Priority", domain.Priority),
        ("IntentSource", domain.IntentSource),
        ("VerificationLevel", domain.VerificationLevel),
        ("Channel", domain.Channel),
        ("OutcomeCode", domain.OutcomeCode),
        ("DeclineReason", domain.DeclineReason),
    ],
)
def test_enum_matches_contract(schema: str, enum_cls: type[StrEnum]) -> None:
    assert [e.value for e in enum_cls] == COMMON[schema]["enum"]


def test_masked_reference_pattern_matches_contract() -> None:
    assert COMMON["Intent"]["properties"]["masked_reference"]["pattern"] == MASKED_REFERENCE.pattern


def test_seed_purpose_codes_are_valid() -> None:
    codes = [c["code"] for c in SEED["codes"]]
    assert len(codes) == len(set(codes))
    for c in SEED["codes"]:
        Priority(c["max_priority"])
        assert 1 <= c["max_duration_min"] <= 120
        assert {Channel(ch) for ch in c["allowed_channels"]}
        assert c["display_text"]["en"] and c["display_text"]["ar"]
        assert len(c["display_text"]["en"]) <= 160 and len(c["display_text"]["ar"]) <= 160


def test_urgent_reserved_for_fraud_and_security() -> None:
    urgent = [c["code"] for c in SEED["codes"] if c["max_priority"] == "URGENT"]
    assert all(code.startswith(("CARD.", "FRAUD.", "SECURITY.")) for code in urgent)


def test_transition_table_matches_canonical_file() -> None:
    from app.modules.intent.domain import TERMINAL_STATUSES
    from app.modules.intent.state_machine import EXPIRABLE, TRANSITIONS

    spec = yaml.safe_load((ROOT / "contracts/state-machines/intent.yaml").read_text())
    expected = {(t["from"], t["to"]): set(t["actors"]) for t in spec["transitions"]}
    actual = {(f.value, t.value): {a.value for a in actors} for (f, t), actors in TRANSITIONS.items()}
    assert actual == expected
    assert {s.value for s in TERMINAL_STATUSES} == set(spec["terminal"])
    assert {s.value for s in EXPIRABLE} == set(spec["expirable"])
