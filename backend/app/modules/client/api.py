"""Client API for the RingSays app and the enterprise SDK (contracts/openapi/client.yaml)."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated, Any, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Header, Query, Request, Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, StringConstraints

from app.core.phone import phone_hash
from app.modules.identity import service as identity
from app.modules.identity.service import ClientPrincipal, DeviceIn
from app.modules.intent.domain import DeclineReason, ResponseAction
from app.platform import ratelimit

from . import service
from .prefs import PreferencesDoc

router = APIRouter(prefix="/v1", tags=["Client"])

E164 = Annotated[str, StringConstraints(pattern=r"^\+[1-9]\d{6,14}$")]
Lang = Literal["en", "ar"]


def clock() -> datetime:
    """Overridden in tests."""
    return datetime.now(UTC)


Now = Annotated[datetime, Depends(clock)]


def current_user(authorization: Annotated[str | None, Header()] = None) -> ClientPrincipal:
    if not authorization or not authorization.lower().startswith("bearer "):
        raise identity.AuthFailed("Missing bearer token")
    return identity.decode_access(authorization.split(" ", 1)[1])


User = Annotated[ClientPrincipal, Depends(current_user)]


def language(accept_language: Annotated[str | None, Header(alias="Accept-Language")] = None) -> Lang:
    """First listed language whose primary subtag is ar or en (q-values in order); default ar."""
    if accept_language:
        ranked = []
        for i, part in enumerate(accept_language.split(",")):
            tag, _, params = part.strip().partition(";")
            q = 1.0
            if params.strip().startswith("q="):
                try:
                    q = float(params.strip()[2:])
                except ValueError:
                    q = 0.0
            ranked.append((-q, i, tag.strip().lower().split("-")[0]))
        for _, _, primary in sorted(ranked):
            if primary in ("ar", "en"):
                return "ar" if primary == "ar" else "en"
    return "ar"


AcceptLanguage = Annotated[Lang, Depends(language)]


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class OtpIn(_Strict):
    phone: E164
    locale: Lang = "ar"


class PushTokensIn(_Strict):
    apns: Annotated[str, StringConstraints(max_length=512)] | None = None
    pushkit: Annotated[str, StringConstraints(max_length=512)] | None = None
    fcm: Annotated[str, StringConstraints(max_length=512)] | None = None


class DeviceRegistrationIn(_Strict):
    platform: Literal["IOS", "ANDROID"]
    public_key: Annotated[str, StringConstraints(min_length=40, max_length=400)]
    app_version: Annotated[str, StringConstraints(max_length=32)]
    push: PushTokensIn | None = None


class VerifyIn(_Strict):
    challenge_id: UUID
    code: Annotated[str, StringConstraints(pattern=r"^\d{6}$")]
    device: DeviceRegistrationIn


class RefreshIn(_Strict):
    refresh_token: Annotated[str, StringConstraints(max_length=200)]
    device_signature: Annotated[str, StringConstraints(max_length=200)]


class SlotIn(_Strict):
    start: datetime
    end: datetime


class RespondIn(_Strict):
    action: ResponseAction
    later_minutes: Literal[15, 30, 60, 120] | None = None
    proposed_slots: Annotated[list[SlotIn], Field(max_length=5)] | None = None
    slot: SlotIn | None = None
    decline_reason: DeclineReason | None = None


def _tokens(t: identity.Tokens) -> dict[str, Any]:
    return {
        "access_token": t.access_token,
        "refresh_token": t.refresh_token,
        "expires_in": t.expires_in,
        "user_id": str(t.user_id),
        "device_id": str(t.device_id),
    }


# Auth


@router.post("/auth/otp", status_code=202)
def request_otp(body: OtpIn, request: Request, now: Now) -> dict[str, Any]:
    client_ip = request.client.host if request.client else "unknown"
    ratelimit.get_limiter().check_otp(phone_hash(body.phone), client_ip, now)
    challenge = identity.request_otp(body.phone, body.locale, now)
    return {"challenge_id": str(challenge), "expires_in": int(identity.OTP_TTL.total_seconds())}


@router.post("/auth/verify")
def verify(body: VerifyIn, now: Now) -> JSONResponse:
    push = body.device.push or PushTokensIn()
    tokens = identity.verify_otp(
        body.challenge_id,
        body.code,
        DeviceIn(
            body.device.platform,
            body.device.public_key,
            body.device.app_version,
            push.apns,
            push.pushkit,
            push.fcm,
        ),
        now,
    )
    return JSONResponse(_tokens(tokens), headers={"Cache-Control": "no-store"})


@router.post("/auth/refresh")
def refresh(body: RefreshIn, now: Now) -> JSONResponse:
    tokens = identity.refresh(body.refresh_token, body.device_signature, now)
    return JSONResponse(_tokens(tokens), headers={"Cache-Control": "no-store"})


@router.post("/auth/logout", status_code=204)
def logout(p: User, now: Now) -> Response:
    identity.sign_out(p, now)
    return Response(status_code=204)


@router.put("/devices/{device_id}/push-tokens", status_code=204)
def update_push_tokens(device_id: UUID, body: PushTokensIn, p: User) -> Response:
    if not identity.update_push_tokens(p, device_id, body.apns, body.pushkit, body.fcm):
        raise service.NotFound("Device not found")
    return Response(status_code=204)


# Inbox and responses


@router.get("/inbox")
def get_inbox(
    p: User,
    lang: AcceptLanguage,
    folder: Literal["REQUESTS", "SCHEDULED", "HISTORY"] | None = None,
    cursor: UUID | None = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> dict[str, Any]:
    return service.inbox(p, folder, limit, cursor, lang)


@router.get("/me/intents/{intent_id}")
def get_my_intent(intent_id: UUID, p: User, lang: AcceptLanguage) -> dict[str, Any]:
    return service.get_display(p, intent_id, lang)


@router.post("/intents/{intent_id}/respond")
def respond(intent_id: UUID, body: RespondIn, p: User, now: Now, lang: AcceptLanguage) -> dict[str, Any]:
    return service.respond(p, intent_id, body.action, body.model_dump(exclude_none=True), now, lang)


# Context Token (enterprise SDK, no RingSays account needed; device id is the SDK install id)

DeviceHeader = Annotated[UUID, Header(alias="RingSays-Device-Id")]


@router.get("/tokens/{token}")
def resolve_token(token: str, device: DeviceHeader, now: Now, lang: AcceptLanguage) -> dict[str, Any]:
    return service.resolve_token(token, device, now, lang)


@router.post("/tokens/{token}/respond")
def respond_via_token(
    token: str, body: RespondIn, device: DeviceHeader, now: Now, lang: AcceptLanguage
) -> dict[str, Any]:
    return service.respond_via_token(
        token, device, body.action, body.model_dump(exclude_none=True), now, lang
    )


# Preferences, consents, export, erasure


def _etag(version: int) -> str:
    return f'W/"v{version}"'


@router.get("/me/preferences")
def get_preferences(p: User) -> JSONResponse:
    doc, version = service.get_preferences(p)
    return JSONResponse(doc, headers={"ETag": _etag(version)})


@router.put("/me/preferences")
def put_preferences(
    body: PreferencesDoc, p: User, now: Now, if_match: Annotated[str, Header(alias="If-Match")]
) -> JSONResponse:
    try:
        version = int(if_match.strip().removeprefix("W/").strip('"').removeprefix("v"))
    except ValueError as exc:
        raise service.PreconditionFailed("If-Match must be an ETag from GET /me/preferences") from exc
    doc, new_version = service.put_preferences(p, body, version, now)
    return JSONResponse(doc, headers={"ETag": _etag(new_version)})


@router.get("/me/consents")
def list_consents(p: User, lang: AcceptLanguage) -> dict[str, Any]:
    return {"items": service.list_consents(p, lang)}


@router.delete("/me/consents/{consent_id}", status_code=204)
def withdraw_consent(consent_id: UUID, p: User, now: Now) -> Response:
    service.withdraw_consent(p, consent_id, now)
    return Response(status_code=204)


@router.post("/me/export")
def export(p: User, now: Now) -> JSONResponse:
    return JSONResponse(service.export(p, now), headers={"Cache-Control": "no-store"})


@router.delete("/me", status_code=202)
def erase(p: User, now: Now) -> Response:
    service.erase(p, now)
    identity.clear_device_cache()
    return Response(status_code=202)
