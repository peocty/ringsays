"""Install real providers from settings (API and worker start). MOCK stays when use_mock_adapters."""

from __future__ import annotations

import logging

import httpx

from app.core.config import settings
from app.modules.delivery import adapters
from app.modules.identity import service as identity

from .push import TIMEOUT, ApnsSender, FcmSender, PlatformPushSender
from .sms import TaqnyatSms, UnifonicSms

log = logging.getLogger(__name__)


def sms_from_settings() -> identity.SmsSender:
    p = settings.sms_provider
    if p == "taqnyat":
        return TaqnyatSms(token=settings.sms_api_key, sender=settings.sms_sender_id)
    if p == "unifonic":
        return UnifonicSms(app_sid=settings.sms_api_key, sender=settings.sms_sender_id)
    raise RuntimeError(f"unknown RINGSAYS_SMS_PROVIDER {p!r}")


def push_from_settings() -> PlatformPushSender:
    # One long lived client per provider (thread safe, pooled): Apple asks senders to keep HTTP/2
    # connections open rather than open one per notification.
    fcm = (
        FcmSender(project_id=settings.fcm_project_id, client=httpx.Client(timeout=TIMEOUT))
        if settings.fcm_project_id
        else None
    )
    apns = (
        ApnsSender(
            key_id=settings.apns_key_id,
            team_id=settings.apns_team_id,
            private_key_pem=settings.apns_private_key,
            topic=settings.apns_topic,
            sandbox=settings.apns_sandbox,
            client=httpx.Client(http2=True, timeout=TIMEOUT),
        )
        if settings.apns_key_id
        else None
    )
    return PlatformPushSender(fcm=fcm, apns=apns)


def install() -> None:
    if settings.use_mock_adapters:
        return
    sms = sms_from_settings()
    push = push_from_settings()
    if push.apns is not None:
        push.apns._auth()  # unreadable Apple key: fail at start, not on every notification
    identity.configure_sms(sms)
    adapters.configure(push=push)
    log.info(
        "providers: sms=%s fcm=%s apns=%s",
        settings.sms_provider,
        bool(settings.fcm_project_id),
        bool(settings.apns_key_id),
    )
