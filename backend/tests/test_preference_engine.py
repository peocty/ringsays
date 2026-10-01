from __future__ import annotations

from datetime import UTC, datetime, time
from zoneinfo import ZoneInfo

import pytest

from app.modules.intent.domain import Priority, VerificationLevel
from app.modules.preference.engine import (
    DAYS,
    Decision,
    IncomingContext,
    Preferences,
    Rule,
    TimeWindow,
    evaluate,
)

RIYADH = ZoneInfo("Asia/Riyadh")
NIGHT = TimeWindow(days=frozenset(DAYS), start=time(22, 0), end=time(7, 0))
WORK = TimeWindow(days=frozenset({"SUN", "MON", "TUE", "WED", "THU"}), start=time(9, 0), end=time(17, 0))


def bank(
    priority: Priority = Priority.NORMAL,
    level: VerificationLevel = VerificationLevel.ORG_AGENT_NUMBER,
    duration: int = 5,
) -> IncomingContext:
    return IncomingContext("BANK", level, priority, duration)


def at(y: int, m: int, d: int, hh: int, mm: int = 0) -> datetime:
    return datetime(y, m, d, hh, mm, tzinfo=RIYADH).astimezone(UTC)


def test_default_allows() -> None:
    assert evaluate(Preferences(), bank(), at(2026, 10, 4, 10)).decision is Decision.ALLOW


def test_night_mode_holds_until_morning_local_time() -> None:
    out = evaluate(Preferences(night_mode=NIGHT), bank(), at(2026, 10, 4, 23, 10))
    assert out.decision is Decision.HOLD_UNTIL_WINDOW
    assert out.hold_until is not None
    assert out.hold_until.astimezone(RIYADH) == datetime(2026, 10, 5, 7, 0, tzinfo=RIYADH)


def test_night_mode_morning_part_of_window() -> None:
    out = evaluate(Preferences(night_mode=NIGHT), bank(), at(2026, 10, 5, 3, 0))
    assert out.decision is Decision.HOLD_UNTIL_WINDOW
    assert out.hold_until is not None and out.hold_until.astimezone(RIYADH).hour == 7


def test_urgent_verified_passes_night_mode_but_unverified_does_not() -> None:
    night = at(2026, 10, 4, 23, 30)
    prefs = Preferences(night_mode=NIGHT)
    assert evaluate(prefs, bank(Priority.URGENT), night).decision is Decision.ALLOW
    unverified = bank(Priority.URGENT, VerificationLevel.PHONE)
    assert evaluate(prefs, unverified, night).decision is Decision.HOLD_UNTIL_WINDOW


def test_verified_only_blocks_unverified_business() -> None:
    prefs = Preferences(verified_businesses_only=True)
    assert evaluate(prefs, bank(level=VerificationLevel.NONE), at(2026, 10, 4, 10)).decision is Decision.BLOCK
    assert evaluate(prefs, bank(), at(2026, 10, 4, 10)).decision is Decision.ALLOW
    family = IncomingContext("FAMILY", VerificationLevel.PHONE, Priority.NORMAL, 5)
    assert evaluate(prefs, family, at(2026, 10, 4, 10)).decision is Decision.ALLOW


def test_window_rule_holds_outside_hours() -> None:
    prefs = Preferences(rules=(Rule(action=Decision.ALLOW, category="BANK", window=WORK),))
    out = evaluate(prefs, bank(), at(2026, 10, 9, 18, 0))  # Friday evening
    assert out.decision is Decision.HOLD_UNTIL_WINDOW
    assert out.hold_until is not None
    assert out.hold_until.astimezone(RIYADH) == datetime(2026, 10, 11, 9, 0, tzinfo=RIYADH)  # Sunday 9:00


def test_duration_rule_requests_first_for_long_calls() -> None:
    prefs = Preferences(
        rules=(
            Rule(action=Decision.ALLOW, max_duration_min=2),
            Rule(action=Decision.REQUEST_FIRST),
        )
    )
    assert evaluate(prefs, bank(duration=2), at(2026, 10, 4, 10)).decision is Decision.ALLOW
    assert evaluate(prefs, bank(duration=10), at(2026, 10, 4, 10)).decision is Decision.REQUEST_FIRST


def test_first_matching_rule_wins() -> None:
    prefs = Preferences(
        rules=(
            Rule(action=Decision.BLOCK, category="SALES"),
            Rule(action=Decision.MESSAGE_ONLY),
        )
    )
    sales = IncomingContext("SALES", VerificationLevel.ORG, Priority.LOW, 5)
    assert evaluate(prefs, sales, at(2026, 10, 4, 10)).decision is Decision.BLOCK
    assert evaluate(prefs, bank(), at(2026, 10, 4, 10)).decision is Decision.MESSAGE_ONLY


def test_naive_now_rejected() -> None:
    with pytest.raises(ValueError):
        evaluate(Preferences(), bank(), datetime(2026, 10, 4, 10))
