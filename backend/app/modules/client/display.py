"""Render intents for the person receiving them (contracts/openapi/client.yaml `IntentDisplay`).

WHY text comes only from the tenant's approved purpose code template, in the receiver's language.
Organisation and agent names come from verified tenant records. Nothing here is free text from a caller,
so an enterprise intent cannot be made to look like something else.
"""

from __future__ import annotations

from collections import defaultdict
from typing import Any
from uuid import UUID

from sqlalchemy import Connection, select

from app.core.db import tenant_tx
from app.core.tables import agents, departments, purpose_codes, tenants
from app.modules.intent.domain import Intent, IntentStatus, ResponseAction
from app.modules.intent.state_machine import TRANSITIONS, A

_ACTION_TARGET = {
    ResponseAction.ACCEPT: IntentStatus.ACCEPTED,
    ResponseAction.LATER: IntentStatus.SCHEDULED,
    ResponseAction.PROPOSE: IntentStatus.RESCHEDULED,
    ResponseAction.SCHEDULE: IntentStatus.SCHEDULED,
    ResponseAction.MESSAGE: IntentStatus.DECLINED,
    ResponseAction.DECLINE: IntentStatus.DECLINED,
}
_ACTION_LABEL = {
    ResponseAction.ACCEPT: "TALK_NOW",
    ResponseAction.LATER: "LATER",
    ResponseAction.PROPOSE: "PROPOSE",
    ResponseAction.SCHEDULE: "SCHEDULE",
    ResponseAction.MESSAGE: "MESSAGE",
    ResponseAction.DECLINE: "DECLINE",
}


def receiver_actions(i: Intent) -> list[str]:
    """Mirrors packages/domain availableReceiverActions."""
    out: list[str] = []
    for action, target in _ACTION_TARGET.items():
        if action is ResponseAction.SCHEDULE and not i.proposed_slots:
            continue
        if i.status is IntentStatus.SCHEDULED:
            if action in (ResponseAction.PROPOSE, ResponseAction.DECLINE):
                out.append(_ACTION_LABEL[action])
            continue
        if i.status is not IntentStatus.DELIVERED:
            continue
        if A.RECEIVER in TRANSITIONS.get((i.status, target), frozenset()):
            out.append(_ACTION_LABEL[action])
    return out


def _slot(s: Any) -> dict[str, str] | None:
    return None if s is None else {"start": s.start.isoformat(), "end": s.end.isoformat()}


def _tenant_strings(conn: Connection, items: list[Intent], lang: str) -> dict[UUID, dict[str, Any]]:
    t = conn.execute(select(tenants.c.legal_name_en, tenants.c.legal_name_ar)).one_or_none()
    codes = {
        r.code: (r.display_en, r.display_ar)
        for r in conn.execute(
            select(purpose_codes.c.code, purpose_codes.c.display_en, purpose_codes.c.display_ar).where(
                purpose_codes.c.code.in_({i.purpose_code for i in items if i.purpose_code})
            )
        )
    }
    depts = {
        r.id: (r.name_en, r.name_ar)
        for r in conn.execute(select(departments.c.id, departments.c.name_en, departments.c.name_ar))
    }
    agent_names = {
        r.agent_id: (r.display_name_en, r.display_name_ar)
        for r in conn.execute(select(agents.c.agent_id, agents.c.display_name_en, agents.c.display_name_ar))
    }
    pick = 1 if lang == "ar" else 0
    out: dict[UUID, dict[str, Any]] = {}
    for i in items:
        out[i.intent_id] = {
            "organisation_name": (t[pick] if t else None),
            "department_name": depts[i.department_id][pick] if i.department_id in depts else None,
            "agent_display_name": agent_names[i.agent_id][pick] if i.agent_id in agent_names else None,
            "why": codes[i.purpose_code][pick] if i.purpose_code in codes else None,
        }
    return out


def render(items: list[Intent], lang: str) -> list[dict[str, Any]]:
    by_tenant: dict[UUID, list[Intent]] = defaultdict(list)
    for i in items:
        if i.tenant_id is not None:
            by_tenant[i.tenant_id].append(i)
    strings: dict[UUID, dict[str, Any]] = {}
    for tenant_id, group in by_tenant.items():
        with tenant_tx(tenant_id) as conn:
            strings.update(_tenant_strings(conn, group, lang))
    return [
        {
            "intent_id": str(i.intent_id),
            "status": i.status.value,
            **strings.get(
                i.intent_id,
                {"organisation_name": None, "department_name": None, "agent_display_name": None, "why": None},
            ),
            "verification_level": i.verification_level.value,
            "intent_source": i.intent_source.value,
            "unverified_subject": i.subject if i.tenant_id is None else None,
            "masked_reference": i.masked_reference,
            "priority": i.priority.value,
            "expected_duration_min": i.expected_duration_min,
            "valid_until": i.valid_until.isoformat(),
            "scheduled_slot": _slot(i.scheduled_slot),
            "proposed_slots": [_slot(s) for s in i.proposed_slots],
            "created_at": i.created_at.isoformat(),
            "deep_link": None,
            "actions": receiver_actions(i),
        }
        for i in items
    ]
