"""Enterprise API routes for intents and purpose codes (contracts/openapi/enterprise.yaml)."""

from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, Header, Query
from fastapi.responses import JSONResponse
from sqlalchemy import Connection

from app.core.auth import Principal, require_scope
from app.core.db import tenant_tx
from app.modules.delivery import adapters
from app.modules.delivery import service as delivery
from app.modules.enterprise import service as enterprise
from app.platform import idempotency

from . import service
from .domain import Intent
from .schemas import IntentCreateIn, OutcomeIn, PurposeCodeIn, ScheduleIn, intent_out

log = logging.getLogger(__name__)
router = APIRouter(prefix="/v1", tags=["Enterprise"])

Writer = Annotated[Principal, Depends(require_scope("intents:write"))]
Reader = Annotated[Principal, Depends(require_scope("intents:read"))]
CatalogueReader = Annotated[Principal, Depends(require_scope("catalogue:read"))]
CatalogueWriter = Annotated[Principal, Depends(require_scope("catalogue:write"))]
IdemKey = Annotated[UUID, Header(alias="Idempotency-Key")]


def clock() -> datetime:
    """Overridden in tests."""
    return datetime.now(UTC)


Now = Annotated[datetime, Depends(clock)]


SECRET_FIELDS = ("context_token",)


def _idempotent(
    p: Principal,
    key: UUID,
    route: str,
    body: Any,
    success_status: int,
    replay_status: int,
    work: Callable[[Connection], dict[str, Any]],
    after_commit: Callable[[], None] | None = None,
) -> JSONResponse:
    """Run `work` once per key. Secret fields (Context Token) are never stored, so a replay returns
    them as null: a token is shown exactly once. `after_commit` runs only for a fresh, committed
    execution (never on replay), for side effects that must not happen if the transaction fails."""
    req_hash = idempotency.request_hash(route, body)
    with tenant_tx(p.tenant_id) as conn:
        stored = idempotency.lookup(conn, p.tenant_id, key, route, req_hash)
        if stored is not None:
            return JSONResponse(stored.body, status_code=replay_status)
        result = work(conn)
        kept = {k: (None if k in SECRET_FIELDS else v) for k, v in result.items()}
        idempotency.store(conn, p.tenant_id, key, route, req_hash, success_status, kept)
    if after_commit is not None:
        try:
            after_commit()
        except Exception as exc:  # best effort side effect; committed result stands
            log.error("after commit step failed: %s", type(exc).__name__)
    return JSONResponse(result, status_code=success_status)


@router.post("/intents", status_code=201)
def create_intent(body: IntentCreateIn, p: Writer, key: IdemKey, now: Now) -> JSONResponse:
    route, payload = "POST /v1/intents", body.model_dump(mode="json")

    # Rate limits are checked inside service.create after validation and under the idempotency lock,
    # so invalid requests and concurrent retries with one key never consume quota.
    def work(conn: Connection) -> dict[str, Any]:
        intent, token = service.create(conn, p.tenant_id, body, p.actor, now)
        return {
            "intent_id": str(intent.intent_id),
            "status": intent.status.value,
            "verification_level": intent.verification_level.value,
            "context_token": token,
        }

    return _idempotent(p, key, route, payload, 201, 200, work)


@router.get("/intents/{intent_id}")
def get_intent(intent_id: UUID, p: Reader) -> dict[str, Any]:
    with tenant_tx(p.tenant_id) as conn:
        return intent_out(service.get(conn, intent_id))


def _transition_route(
    p: Principal,
    key: UUID,
    intent_id: UUID,
    name: str,
    status: int,
    fn: Callable[[Connection], Intent],
    body: Any = None,
    after_commit: Callable[[], None] | None = None,
) -> JSONResponse:
    route = f"POST /v1/intents/{intent_id}/{name}"
    return _idempotent(p, key, route, body, status, status, lambda conn: intent_out(fn(conn)), after_commit)


@router.post("/intents/{intent_id}/cancel")
def cancel_intent(intent_id: UUID, p: Writer, key: IdemKey, now: Now) -> JSONResponse:
    return _transition_route(
        p, key, intent_id, "cancel", 200, lambda c: service.cancel(c, intent_id, p.actor, now)
    )


@router.post("/intents/{intent_id}/calling", status_code=202)
def signal_calling(intent_id: UUID, p: Writer, key: IdemKey, now: Now) -> JSONResponse:
    def push() -> None:
        delivery.precall_push_after_commit(
            p.tenant_id, intent_id, adapters.get_directory(), adapters.get_push_sender(), now
        )

    return _transition_route(
        p,
        key,
        intent_id,
        "calling",
        202,
        lambda c: service.signal_calling(c, intent_id, p.actor, now),
        after_commit=push,
    )


@router.post("/intents/{intent_id}/schedule")
def schedule_intent(intent_id: UUID, body: ScheduleIn, p: Writer, key: IdemKey, now: Now) -> JSONResponse:
    return _transition_route(
        p,
        key,
        intent_id,
        "schedule",
        200,
        lambda c: service.schedule(c, intent_id, body.slot.to_domain(), p.actor, now),
        body.model_dump(mode="json"),
    )


@router.post("/intents/{intent_id}/outcome")
def record_outcome(intent_id: UUID, body: OutcomeIn, p: Writer, key: IdemKey, now: Now) -> JSONResponse:
    return _transition_route(
        p,
        key,
        intent_id,
        "outcome",
        200,
        lambda c: service.record_outcome(c, intent_id, body.code, p.actor, now),
        body.model_dump(mode="json"),
    )


@router.get("/purpose-codes")
def list_purpose_codes(
    p: CatalogueReader,
    cursor: str | None = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> dict[str, Any]:
    with tenant_tx(p.tenant_id) as conn:
        items = enterprise.list_purpose_codes(conn, limit + 1, cursor)
    next_cursor = items[limit - 1]["code"] if len(items) > limit else None
    return {"items": items[:limit], "next_cursor": next_cursor}


@router.post("/purpose-codes", status_code=201)
def propose_purpose_code(body: PurposeCodeIn, p: CatalogueWriter, key: IdemKey) -> JSONResponse:
    payload = body.model_dump(mode="json")
    return _idempotent(
        p,
        key,
        "POST /v1/purpose-codes",
        payload,
        201,
        201,
        lambda conn: enterprise.propose_purpose_code(conn, p.tenant_id, payload),
    )
