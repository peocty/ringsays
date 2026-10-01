"""Database backed recipient directory: phone number to RingSays user, devices and preferences.

Used by the delivery worker (worker role). Looks users up by keyed phone hash only. If the user withdrew
consent for the sending organisation, the recipient is marked blocked, so app channels are skipped
(an ordinary phone call cannot be stopped by RingSays and is not affected).
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy import Engine, select

from app.core.db import worker_tx
from app.core.phone import phone_hash
from app.core.tables import consents, devices, preferences, users
from app.modules.delivery.adapters import PushTarget, Recipient

from . import service
from .prefs import to_engine


class DbRecipientDirectory:
    def __init__(self, engine: Engine | None = None) -> None:
        self._engine = engine

    def lookup(self, phone_e164: str, tenant_id: UUID | None) -> Recipient | None:
        with worker_tx(self._engine) as conn:
            user = conn.execute(
                select(users.c.id).where(
                    users.c.phone_hash == phone_hash(phone_e164), users.c.erased_at.is_(None)
                )
            ).one_or_none()
            if user is None:
                return None
            devs = conn.execute(
                select(devices).where(devices.c.user_id == user.id, devices.c.revoked_at.is_(None))
            ).all()
            doc = conn.execute(
                select(preferences.c.document).where(preferences.c.user_id == user.id)
            ).scalar_one_or_none()
            withdrawn = (
                tenant_id is not None
                and conn.execute(
                    select(consents.c.id).where(
                        consents.c.user_id == user.id,
                        consents.c.tenant_id == tenant_id,
                        consents.c.withdrawn_at.is_not(None),
                    )
                ).first()
                is not None
            )
        targets = []
        for d in devs:
            token = d.apns_token if d.platform == "IOS" else d.fcm_token
            if token:
                targets.append(PushTarget(device_id=d.id, platform=d.platform, token=token))
        return Recipient(
            user_ref=user.id, devices=tuple(targets), preferences=to_engine(doc or {}), blocked=withdrawn
        )

    def note_contact(
        self, recipient: Recipient, tenant_id: UUID, basis_ref: str | None, now: datetime
    ) -> None:
        with worker_tx(self._engine) as conn:
            service.record_contact(conn, recipient.user_ref, tenant_id, basis_ref, now)
