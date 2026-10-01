"""Receiver communication rules, evaluated at delivery time. Pure functions; no I/O.

Rules mirror contracts/openapi/client.yaml `Preferences`. First matching rule wins; built in rules
(verified businesses only, night mode) apply before user rules. URGENT intents from verified
organisations pass night mode, because tenant URGENT use is capped by purpose code and daily quota.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, time, timedelta
from enum import StrEnum
from zoneinfo import ZoneInfo

from app.modules.intent.domain import Priority, VerificationLevel

DAYS = ("MON", "TUE", "WED", "THU", "FRI", "SAT", "SUN")
_VERIFICATION_RANK = {
    VerificationLevel.NONE: 0,
    VerificationLevel.PHONE: 1,
    VerificationLevel.ORG: 2,
    VerificationLevel.ORG_AGENT_NUMBER: 3,
}


class Decision(StrEnum):
    ALLOW = "ALLOW"
    HOLD_UNTIL_WINDOW = "HOLD_UNTIL_WINDOW"
    REQUEST_FIRST = "REQUEST_FIRST"
    MESSAGE_ONLY = "MESSAGE_ONLY"
    BLOCK = "BLOCK"


@dataclass(frozen=True, slots=True)
class TimeWindow:
    days: frozenset[str]
    start: time
    end: time  # end before start means window crosses midnight

    def contains(self, local: datetime) -> bool:
        t = local.time()
        day = DAYS[local.weekday()]
        if self.start <= self.end:
            return day in self.days and self.start <= t < self.end
        # Crosses midnight: evening part belongs to start day, morning part to previous day.
        if t >= self.start:
            return day in self.days
        prev = DAYS[(local.weekday() - 1) % 7]
        return t < self.end and prev in self.days

    def next_exit(self, local: datetime) -> datetime:
        """Earliest moment at or after `local` outside this window (window end is exclusive)."""
        if not self.contains(local):
            return local
        for d in range(9):
            candidate = datetime.combine(local.date() + timedelta(days=d), self.end, local.tzinfo)
            if candidate > local and not self.contains(candidate):
                return candidate
        raise ValueError("window never closes")

    def next_entry(self, local: datetime) -> datetime:
        """Earliest moment at or after `local` inside this window."""
        if self.contains(local):
            return local
        for d in range(9):
            candidate = datetime.combine(local.date() + timedelta(days=d), self.start, local.tzinfo)
            if candidate >= local and self.contains(candidate):
                return candidate
        raise ValueError("window never opens")


@dataclass(frozen=True, slots=True)
class Rule:
    action: Decision
    category: str | None = None
    min_verification: VerificationLevel | None = None
    min_priority: Priority | None = None
    max_duration_min: int | None = None
    window: TimeWindow | None = None


@dataclass(frozen=True, slots=True)
class Preferences:
    timezone: str = "Asia/Riyadh"
    verified_businesses_only: bool = False
    night_mode: TimeWindow | None = None
    rules: tuple[Rule, ...] = field(default=())


@dataclass(frozen=True, slots=True)
class IncomingContext:
    category: str  # BANK, GOVERNMENT, WORK, FAMILY, UNKNOWN, SALES, DELIVERY
    verification: VerificationLevel
    priority: Priority
    expected_duration_min: int


@dataclass(frozen=True, slots=True)
class Outcome:
    decision: Decision
    reason: str
    hold_until: datetime | None = None


def evaluate(prefs: Preferences, ctx: IncomingContext, now: datetime) -> Outcome:
    if now.tzinfo is None:
        raise ValueError("now must be timezone aware")
    tz = ZoneInfo(prefs.timezone)
    local = now.astimezone(tz)
    verified = _VERIFICATION_RANK[ctx.verification] >= _VERIFICATION_RANK[VerificationLevel.ORG]

    if prefs.verified_businesses_only and ctx.category not in ("FAMILY", "WORK") and not verified:
        return Outcome(Decision.BLOCK, "verified businesses only")

    if prefs.night_mode is not None and prefs.night_mode.contains(local):
        if ctx.priority is Priority.URGENT and verified:
            return Outcome(Decision.ALLOW, "urgent from verified organisation passes night mode")
        until = prefs.night_mode.next_exit(local)
        return Outcome(Decision.HOLD_UNTIL_WINDOW, "night mode", until.astimezone(now.tzinfo))

    for rule in prefs.rules:
        if not _matches(rule, ctx):
            continue
        if rule.window is not None and not rule.window.contains(local):
            if rule.action is Decision.ALLOW:
                until = rule.window.next_entry(local)
                return Outcome(
                    Decision.HOLD_UNTIL_WINDOW, "outside allowed window", until.astimezone(now.tzinfo)
                )
            continue
        return Outcome(rule.action, f"rule for {rule.category or 'any'}")

    return Outcome(Decision.ALLOW, "default")


def _matches(rule: Rule, ctx: IncomingContext) -> bool:
    if rule.category is not None and rule.category != ctx.category:
        return False
    if rule.min_verification is not None and (
        _VERIFICATION_RANK[ctx.verification] < _VERIFICATION_RANK[rule.min_verification]
    ):
        return False
    if rule.min_priority is not None and ctx.priority.rank < rule.min_priority.rank:
        return False
    return not (rule.max_duration_min is not None and ctx.expected_duration_min > rule.max_duration_min)
