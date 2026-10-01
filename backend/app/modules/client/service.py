"""Client API service: inbox, responses, preferences, consents, export and erasure for RingSays users.

Access model:
- Reads run in `user_tx`: own identity rows plus intents addressed to the user's phone (read only policy).
- Responses check ownership in `user_tx`, then change the intent inside its tenant's scope, so tenant
  events, webhooks and audit are written exactly as for any other change.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import Connection, delete, insert, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert

from app.core.db import anonymous_tx, tenant_tx, user_tx
from app.core.phone import decrypt_phone
from app.core.tables import consents, devices, intents, preferences, refresh_tokens, tenants, users
from app.modules.audit import service as audit
from app.modules.context import service as context
from app.modules.identity import service as identity_service
from app.modules.identity.service import ClientPrincipal
from app.modules.intent import repo
from app.modules.intent import service as intent_service
from app.modules.intent import state_machine as sm
from app.modules.intent.domain import Channel, DeclineReason, Intent, ResponseAction, Slot
from app.modules.intent.errors import IntentError, RuleViolation

from . import display
from .prefs import DEFAULT_DOC, PreferencesDoc

APP_CHANNELS = (Channel.SDK.value, Channel.PRECALL_PUSH.value, Channel.VOIP.value)
FOLDERS: dict[str, Sequence[str]] = {
    "REQUESTS": ("DELIVERED", "RESCHEDULED"),
    "SCHEDULED": ("ACCEPTED", "SCHEDULED", "IN_PROGRESS"),
    "HISTORY": ("COMPLETED", "FOLLOW_UP_REQUIRED", "DECLINED", "CANCELLED", "EXPIRED"),
}


class PreconditionFailed(IntentError):
    code = "precondition_failed"
    http_status = 412


class NotFound(IntentError):
    code = "not_found"
    http_status = 404


# Inbox and display


def inbox(
    p: ClientPrincipal, folder: str | None, limit: int, cursor: UUID | None, lang: str
) -> dict[str, Any]:
    """Intents delivered to this user through an app channel. Undelivered intents (window not open, held
    by rules) and plain call fallbacks never appear."""
    statuses = FOLDERS.get(folder or "", tuple(s for f in FOLDERS.values() for s in f))
    with user_tx(p.user_id, p.phone_hash, p.since) as conn:
        withdrawn = select(consents.c.tenant_id).where(
            consents.c.user_id == p.user_id, consents.c.withdrawn_at.is_not(None)
        )
        q = (
            select(intents.c.id)
            .where(
                intents.c.to_phone_hash == p.phone_hash,
                intents.c.created_at >= p.since,
                intents.c.status.in_(statuses),
                intents.c.channel_used.in_(APP_CHANNELS),
                intents.c.tenant_id.not_in(withdrawn),
            )
            .order_by(intents.c.id.desc())
            .limit(limit + 1)
        )
        if cursor is not None:
            q = q.where(intents.c.id < cursor)
        ids = conn.execute(q).scalars().all()
        items = [repo.load(conn, i)[0] for i in ids[:limit]]
    return {
        "items": display.render(items, lang),
        "next_cursor": str(ids[limit - 1]) if len(ids) > limit else None,
    }


def _own_intent(p: ClientPrincipal, intent_id: UUID) -> Intent:
    with user_tx(p.user_id, p.phone_hash, p.since) as conn:
        try:
            intent, _ = repo.load(conn, intent_id)
        except repo.IntentNotFound as exc:
            raise NotFound("Intent not found") from exc
    if intent.channel_used is None or intent.channel_used.value not in APP_CHANNELS:
        raise NotFound("Intent not found")
    return intent


def get_display(p: ClientPrincipal, intent_id: UUID, lang: str) -> dict[str, Any]:
    return display.render([_own_intent(p, intent_id)], lang)[0]


# Responses


def _respond_fn(action: ResponseAction, now: datetime, body: dict[str, Any]) -> Any:
    def fn(i: Intent) -> Intent:
        slots = [Slot(start=s["start"], end=s["end"]) for s in body.get("proposed_slots") or []]
        slot = body.get("slot")
        reason = body.get("decline_reason")
        return sm.respond(
            i,
            action,
            now,
            later_minutes=body.get("later_minutes"),
            proposed_slots=slots,
            slot=Slot(start=slot["start"], end=slot["end"]) if slot else None,
            decline_reason=DeclineReason(reason) if reason else None,
        )

    return fn


def respond(
    p: ClientPrincipal,
    intent_id: UUID,
    action: ResponseAction,
    body: dict[str, Any],
    now: datetime,
    lang: str,
) -> dict[str, Any]:
    owned = _own_intent(p, intent_id)
    if owned.tenant_id is None:
        raise RuleViolation("consumer intents arrive in MVP 2")
    with tenant_tx(owned.tenant_id) as conn:
        after = intent_service.apply_receiver(conn, intent_id, _respond_fn(action, now, body), p.actor, now)
    return display.render([after], lang)[0]


def resolve_token(token: str, device_id: UUID, now: datetime, lang: str) -> dict[str, Any]:
    from app.modules.delivery import service as delivery

    with anonymous_tx() as conn:
        intent = delivery.deliver_via_sdk(conn, token, device_id, now)
    return display.render([intent], lang)[0]


def respond_via_token(
    token: str, device_id: UUID, action: ResponseAction, body: dict[str, Any], now: datetime, lang: str
) -> dict[str, Any]:
    with anonymous_tx() as conn:
        tenant_id, intent_id = context.resolve(conn, token, device_id, now)
    with tenant_tx(tenant_id) as conn:
        after = intent_service.apply_receiver(
            conn, intent_id, _respond_fn(action, now, body), f"sdk_device:{device_id}", now
        )
    return display.render([after], lang)[0]


# Preferences


def get_preferences(p: ClientPrincipal) -> tuple[dict[str, Any], int]:
    with user_tx(p.user_id, p.phone_hash, p.since) as conn:
        row = conn.execute(select(preferences).where(preferences.c.user_id == p.user_id)).one_or_none()
    return (dict(row.document), row.version) if row else (dict(DEFAULT_DOC), 0)


def put_preferences(
    p: ClientPrincipal, doc: PreferencesDoc, if_match_version: int, now: datetime
) -> tuple[dict[str, Any], int]:
    document = doc.model_dump(mode="json")
    with user_tx(p.user_id, p.phone_hash, p.since) as conn:
        row = conn.execute(
            select(preferences.c.version).where(preferences.c.user_id == p.user_id).with_for_update()
        ).one_or_none()
        current = row.version if row else 0
        if current != if_match_version:
            raise PreconditionFailed("Preferences changed on another device; reload and retry")
        if row is None:
            # Two first saves at once: only one insert wins; the other gets 412 like any stale write.
            created = conn.execute(
                pg_insert(preferences)
                .values(user_id=p.user_id, document=document, version=1, updated_at=now)
                .on_conflict_do_nothing(index_elements=[preferences.c.user_id])
                .returning(preferences.c.user_id)
            ).first()
            if created is None:
                raise PreconditionFailed("Preferences changed on another device; reload and retry")
        else:
            conn.execute(
                update(preferences)
                .where(preferences.c.user_id == p.user_id)
                .values(document=document, version=current + 1, updated_at=now)
            )
    return document, current + 1


# Consents


def list_consents(p: ClientPrincipal, lang: str) -> list[dict[str, Any]]:
    with user_tx(p.user_id, p.phone_hash, p.since) as conn:
        rows = conn.execute(
            select(consents).where(consents.c.user_id == p.user_id).order_by(consents.c.granted_at)
        ).all()
    out = []
    for r in rows:
        with tenant_tx(r.tenant_id) as conn:
            name = conn.execute(select(tenants.c.legal_name_en, tenants.c.legal_name_ar)).one_or_none()
        out.append(
            {
                "consent_id": str(r.id),
                "grantee": (name[1] if lang == "ar" else name[0]) if name else None,
                "purpose": r.purpose,
                "scope": "contact through RingSays app",
                "granted_at": r.granted_at.isoformat(),
                "withdrawn_at": r.withdrawn_at.isoformat() if r.withdrawn_at else None,
            }
        )
    return out


def withdraw_consent(p: ClientPrincipal, consent_id: UUID, now: datetime) -> None:
    with user_tx(p.user_id, p.phone_hash, p.since) as conn:
        result = conn.execute(
            update(consents)
            .where(consents.c.id == consent_id, consents.c.withdrawn_at.is_(None))
            .values(withdrawn_at=now)
        )
        if result.rowcount != 1:
            raise NotFound("Consent not found or already withdrawn")
        tenant_id = conn.execute(select(consents.c.tenant_id).where(consents.c.id == consent_id)).scalar_one()
    with tenant_tx(tenant_id) as conn:
        audit.append(
            conn,
            tenant_id=tenant_id,
            actor=p.actor,
            action="consent.withdraw",
            object_type="consent",
            object_id=str(consent_id),
            at=now,
        )


def record_contact(
    conn: Connection, user_id: UUID, tenant_id: UUID, basis_ref: str | None, now: datetime
) -> None:
    """Worker: first app delivery from a tenant creates the user's consent ledger entry for it."""
    exists = conn.execute(
        select(consents.c.id).where(consents.c.user_id == user_id, consents.c.tenant_id == tenant_id)
    ).first()
    if exists is None:
        conn.execute(
            insert(consents).values(
                id=uuid4(),
                user_id=user_id,
                tenant_id=tenant_id,
                purpose="service communication",
                basis_ref=basis_ref,
                granted_at=now,
            )
        )


# Export and erasure


def export(p: ClientPrincipal, now: datetime) -> dict[str, Any]:
    with user_tx(p.user_id, p.phone_hash, p.since) as conn:
        u = conn.execute(select(users).where(users.c.id == p.user_id)).one()
        devs = conn.execute(select(devices).where(devices.c.user_id == p.user_id)).all()
        pref = conn.execute(
            select(preferences.c.document).where(preferences.c.user_id == p.user_id)
        ).scalar_one_or_none()
        received = conn.execute(
            select(
                intents.c.id,
                intents.c.tenant_id,
                intents.c.purpose_code,
                intents.c.status,
                intents.c.channel_used,
                intents.c.created_at,
            )
            .where(
                intents.c.to_phone_hash == p.phone_hash,
                intents.c.created_at >= p.since,
                intents.c.channel_used.in_(APP_CHANNELS),
            )
            .order_by(intents.c.created_at)
        ).all()
    return {
        "generated_at": now.isoformat(),
        "profile": {
            "user_id": str(u.id),
            "phone": decrypt_phone(u.phone_ciphertext) if u.phone_ciphertext else None,
            "display_name": u.display_name,
            "locale": u.locale,
            "created_at": u.created_at.isoformat(),
        },
        "devices": [
            {
                "device_id": str(d.id),
                "platform": d.platform,
                "app_version": d.app_version,
                "created_at": d.created_at.isoformat(),
                "signed_out_at": d.revoked_at.isoformat() if d.revoked_at else None,
            }
            for d in devs
        ],
        "preferences": pref or DEFAULT_DOC,
        "consents": list_consents(p, "en"),
        "communications_received": [
            {
                "intent_id": str(r.id),
                "organisation_id": str(r.tenant_id),
                "purpose_code": r.purpose_code,
                "status": r.status,
                "channel": r.channel_used,
                "received_at": r.created_at.isoformat(),
            }
            for r in received
        ],
        "address_book": "never collected",
    }


def erase(p: ClientPrincipal, now: datetime) -> None:
    """Delete personal data held for the user. Intents remain with the organisation that sent them
    (RingSays processes them on that organisation's behalf). Any later account on the same number sees
    only intents created after it was opened, so erased history is never shown to anyone again."""
    with user_tx(p.user_id, p.phone_hash, p.since) as conn:
        device_ids = list(conn.execute(select(devices.c.id).where(devices.c.user_id == p.user_id)).scalars())
        conn.execute(
            update(refresh_tokens).where(refresh_tokens.c.revoked_at.is_(None)).values(revoked_at=now)
        )
        conn.execute(
            update(devices)
            .where(devices.c.revoked_at.is_(None))
            .values(revoked_at=now, apns_token=None, pushkit_token=None, fcm_token=None, public_key="erased")
        )
        conn.execute(delete(preferences).where(preferences.c.user_id == p.user_id))
        conn.execute(delete(consents).where(consents.c.user_id == p.user_id))
        conn.execute(
            update(users)
            .where(users.c.id == p.user_id)
            .values(phone_hash=f"erased:{p.user_id}", phone_ciphertext=None, display_name=None, erased_at=now)
        )
        # Per user audit chain (tenant_id NULL, object = user): readable only in this user's scope.
        audit.append(
            conn,
            tenant_id=None,
            actor=f"user:{p.user_id}",
            action="user.erase",
            object_type="user",
            object_id=str(p.user_id),
            at=now,
        )
    identity_service.revoke_devices_everywhere(device_ids)
