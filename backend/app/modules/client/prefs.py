"""Receiver preferences document (client.yaml `Preferences`) and conversion to engine rules."""

from __future__ import annotations

from datetime import time
from typing import Annotated, Any, Literal
from zoneinfo import available_timezones

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, field_validator

from app.modules.intent.domain import Priority, VerificationLevel
from app.modules.preference import engine

_ZONES = frozenset(z for z in available_timezones() if "/" in z and not z.startswith("Etc/")) | {"UTC"}
Day = Literal["MON", "TUE", "WED", "THU", "FRI", "SAT", "SUN"]
HHMM = Annotated[str, StringConstraints(pattern=r"^([01]\d|2[0-3]):[0-5]\d$")]
Category = Literal["WORK", "FAMILY", "UNKNOWN", "SALES", "DELIVERY", "BANK", "GOVERNMENT"]
Action = Literal["ALLOW", "HOLD_UNTIL_WINDOW", "REQUEST_FIRST", "MESSAGE_ONLY", "BLOCK"]


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class TimeWindowDoc(_Strict):
    days: Annotated[list[Day], Field(min_length=1, max_length=7)]
    start: HHMM
    end: HHMM

    @field_validator("end")
    @classmethod
    def _not_empty(cls, v: str, info: Any) -> str:
        if info.data.get("start") == v:
            raise ValueError("window start and end must differ")
        return v


class RuleMatch(_Strict):
    category: Category | None = None
    min_verification: VerificationLevel | None = None
    min_priority: Priority | None = None
    max_duration_min: Annotated[int, Field(ge=1, le=120)] | None = None


class RuleDoc(_Strict):
    match: RuleMatch
    window: TimeWindowDoc | None = None
    action: Action


class PreferencesDoc(_Strict):
    timezone: Annotated[str, StringConstraints(max_length=64)] = "Asia/Riyadh"
    verified_businesses_only: bool = False
    night_mode: TimeWindowDoc | None = None
    rules: Annotated[list[RuleDoc], Field(max_length=50)] = []

    @field_validator("timezone")
    @classmethod
    def _known_zone(cls, v: str) -> str:
        if v not in _ZONES:
            raise ValueError("unknown timezone; use an IANA name such as Asia/Riyadh")
        return v


DEFAULT_DOC = PreferencesDoc().model_dump(mode="json")


def _window(w: TimeWindowDoc | None) -> engine.TimeWindow | None:
    if w is None:
        return None
    return engine.TimeWindow(
        days=frozenset(w.days), start=time.fromisoformat(w.start), end=time.fromisoformat(w.end)
    )


def to_engine(document: dict[str, Any]) -> engine.Preferences:
    """Stored documents were validated on write; re-validated here so a bad row can never crash delivery
    (falls back to defaults instead)."""
    try:
        doc = PreferencesDoc.model_validate(document)
    except ValueError:
        doc = PreferencesDoc()
    return engine.Preferences(
        timezone=doc.timezone,
        verified_businesses_only=doc.verified_businesses_only,
        night_mode=_window(doc.night_mode),
        rules=tuple(
            engine.Rule(
                action=engine.Decision(r.action),
                category=r.match.category,
                min_verification=r.match.min_verification,
                min_priority=r.match.min_priority,
                max_duration_min=r.match.max_duration_min,
                window=_window(r.window),
            )
            for r in doc.rules
        ),
    )
