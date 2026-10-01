"""Catalogue and integration management for the portal: purpose codes, API credentials, webhooks."""

from __future__ import annotations

import secrets
from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import Connection, func, select, text, update

from app.core import auth
from app.core.config import settings
from app.core.secrets import generate_secret, hash_secret
from app.core.tables import purpose_codes, webhook_deliveries, webhook_endpoints
from app.modules.enterprise import service as enterprise
from app.modules.webhooks import service as webhooks

from .access import Conflict, Member, NotFound
from .org import _audit, iso

# Purpose codes


def code_out(r: Any) -> dict[str, Any]:
    return {
        "code": r.code,
        "display_text": {"en": r.display_en, "ar": r.display_ar},
        "max_priority": r.max_priority,
        "max_duration_min": r.max_duration_min,
        "allowed_channels": list(r.allowed_channels),
        "status": r.status,
        "version": r.version,
        "proposed_at": iso(r.proposed_at),
        "reviewed_at": iso(r.reviewed_at),
        "review_reason": r.review_reason,
    }


def list_codes(conn: Connection) -> list[dict[str, Any]]:
    return [code_out(r) for r in conn.execute(select(purpose_codes).order_by(purpose_codes.c.code)).all()]


def propose_code(conn: Connection, m: Member, body: dict[str, Any], now: datetime) -> dict[str, Any]:
    enterprise.propose_purpose_code(conn, m.tenant_id, body)
    _audit(conn, m, "purpose_code.propose", "purpose_code", body["code"], now)
    return code_out(conn.execute(select(purpose_codes).where(purpose_codes.c.code == body["code"])).one())


def retire_code(conn: Connection, m: Member, code: str, reason: str, now: datetime) -> dict[str, Any]:
    row = conn.execute(
        update(purpose_codes)
        .where(purpose_codes.c.code == code)
        .values(status="RETIRED")
        .returning(*purpose_codes.c)
    ).one_or_none()
    if row is None:
        raise NotFound("Purpose code not found")
    _audit(conn, m, "purpose_code.retire", "purpose_code", code, now, reason)
    return code_out(row)


# API credentials


def client_out(r: Any) -> dict[str, Any]:
    return {
        "client_id": r.client_id,
        "label": r.label,
        "scopes": sorted(r.scopes),
        "active": r.active,
        "created_at": iso(r.created_at),
        "revoked_at": iso(r.revoked_at),
    }


def list_clients(conn: Connection) -> list[dict[str, Any]]:
    rows = conn.execute(text("SELECT * FROM enterprise.tenant_api_clients()")).all()
    return [client_out(r) for r in rows]


def create_client(
    conn: Connection, m: Member, label: str, scopes: list[str], now: datetime
) -> dict[str, Any]:
    client_id = "cli_" + secrets.token_hex(8)
    secret = generate_secret()
    created = conn.execute(
        text("SELECT enterprise.create_tenant_api_client(:c, :h, CAST(:s AS text[]), :l, :by, :max) AS ok"),
        {
            "c": client_id,
            "h": hash_secret(secret),
            "s": sorted(set(scopes)),
            "l": label,
            "by": m.actor,
            "max": settings.api_clients_max_active,
        },
    ).scalar_one()
    if not created:
        raise Conflict(f"At most {settings.api_clients_max_active} active credentials; revoke one first")
    _audit(conn, m, "api_client.create", "api_client", client_id, now, reason=",".join(sorted(scopes)))
    row = next(
        r
        for r in conn.execute(text("SELECT * FROM enterprise.tenant_api_clients()")).all()
        if r.client_id == client_id
    )
    return {**client_out(row), "client_secret": secret}


def revoke_client(conn: Connection, m: Member, client_id: str, reason: str, now: datetime) -> dict[str, Any]:
    found = conn.execute(
        text("SELECT enterprise.revoke_tenant_api_client(:c, :n)"), {"c": client_id, "n": now}
    ).scalar_one()
    if not found:
        raise NotFound("API client not found")
    _audit(conn, m, "api_client.revoke", "api_client", client_id, now, reason)
    # This process forgets the client at once; other API processes within their 30 second status cache.
    auth._status_cache.pop(client_id, None)
    row = next(
        r
        for r in conn.execute(text("SELECT * FROM enterprise.tenant_api_clients()")).all()
        if r.client_id == client_id
    )
    return client_out(row)


# Webhooks


def endpoint_out(r: Any) -> dict[str, Any]:
    return {
        "endpoint_id": str(r.id),
        "url": r.url,
        "events": list(r.events),
        "active": r.active,
        "created_at": iso(r.created_at),
    }


def list_endpoints(conn: Connection) -> list[dict[str, Any]]:
    rows = conn.execute(select(webhook_endpoints).order_by(webhook_endpoints.c.created_at)).all()
    return [endpoint_out(r) for r in rows]


def create_endpoint(
    conn: Connection, m: Member, url: str, events: list[str], now: datetime
) -> dict[str, Any]:
    conn.execute(text("SELECT pg_advisory_xact_lock(hashtext(:k))"), {"k": f"webhooks:{m.tenant_id}"})
    active = conn.execute(
        select(func.count()).select_from(webhook_endpoints).where(webhook_endpoints.c.active)
    ).scalar_one()
    if active >= settings.webhook_endpoints_max_active:
        raise Conflict(f"At most {settings.webhook_endpoints_max_active} active endpoints; disable one first")
    endpoint_id, secret = webhooks.create_endpoint(conn, m.tenant_id, url, events)
    _audit(conn, m, "webhook_endpoint.create", "webhook_endpoint", str(endpoint_id), now)
    row = conn.execute(select(webhook_endpoints).where(webhook_endpoints.c.id == endpoint_id)).one()
    return {**endpoint_out(row), "secret": secret}


def disable_endpoint(
    conn: Connection, m: Member, endpoint_id: UUID, reason: str, now: datetime
) -> dict[str, Any]:
    row = conn.execute(
        update(webhook_endpoints)
        .where(webhook_endpoints.c.id == endpoint_id)
        .values(active=False)
        .returning(*webhook_endpoints.c)
    ).one_or_none()
    if row is None:
        raise NotFound("Webhook endpoint not found")
    _audit(conn, m, "webhook_endpoint.disable", "webhook_endpoint", str(endpoint_id), now, reason)
    return endpoint_out(row)


def delivery_out(r: Any) -> dict[str, Any]:
    return {
        "delivery_id": r.id,
        "endpoint_id": str(r.endpoint_id),
        "event_id": str(r.event_id),
        "event_type": r.event_type,
        "intent_id": str(r.intent_id),
        "status": r.status,
        "attempts": r.attempts,
        "next_attempt_at": iso(r.next_attempt_at) if r.status in ("PENDING", "SENDING") else None,
        "last_status_code": r.last_status_code,
        "last_error": r.last_error,
        "created_at": iso(r.created_at),
        "delivered_at": iso(r.delivered_at),
    }


def list_deliveries(
    conn: Connection, endpoint_id: UUID, status: str | None, limit: int, cursor: int | None
) -> dict[str, Any]:
    if (
        conn.execute(select(webhook_endpoints.c.id).where(webhook_endpoints.c.id == endpoint_id)).first()
        is None
    ):
        raise NotFound("Webhook endpoint not found")
    q = (
        select(webhook_deliveries)
        .where(webhook_deliveries.c.endpoint_id == endpoint_id)
        .order_by(webhook_deliveries.c.id.desc())
        .limit(limit + 1)
    )
    if status:
        q = q.where(webhook_deliveries.c.status == status)
    if cursor:
        q = q.where(webhook_deliveries.c.id < cursor)
    rows = conn.execute(q).all()
    items = [delivery_out(r) for r in rows[:limit]]
    return {"items": items, "next_cursor": str(items[-1]["delivery_id"]) if len(rows) > limit else None}


def replay_delivery(conn: Connection, m: Member, delivery_id: int, now: datetime) -> dict[str, Any]:
    row = conn.execute(
        select(webhook_deliveries, webhook_endpoints.c.active.label("endpoint_active"))
        .join(webhook_endpoints, webhook_endpoints.c.id == webhook_deliveries.c.endpoint_id)
        .where(webhook_deliveries.c.id == delivery_id)
        .with_for_update(of=webhook_deliveries)
    ).one_or_none()
    if row is None:
        raise NotFound("Webhook delivery not found")
    if row.status not in ("DELIVERED", "DEAD"):
        raise Conflict("Only delivered or dead webhooks can be replayed; this one is still queued")
    if not row.endpoint_active:
        raise Conflict("Endpoint is disabled")
    webhooks.replay(conn, delivery_id, now)
    _audit(conn, m, "webhook_delivery.replay", "webhook_delivery", str(delivery_id), now)
    return delivery_out(
        conn.execute(select(webhook_deliveries).where(webhook_deliveries.c.id == delivery_id)).one()
    )
