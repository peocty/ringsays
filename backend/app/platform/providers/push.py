"""Push providers: Firebase Cloud Messaging (Android) and Apple Push Notification service (iOS).

Push passes through Google and Apple outside the Kingdom, so a push carries only the intent id and its
kind; the app fetches everything else over the RingSays API after it wakes. The visible text is a fixed
bilingual line with no organisation name, reason or reference.
"""

from __future__ import annotations

import logging
import re
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from uuid import UUID

import httpx
import jwt

from app.modules.delivery.adapters import PushResult, PushTarget

log = logging.getLogger(__name__)
TIMEOUT = httpx.Timeout(5.0, connect=3.0)
_APNS_TOKEN = re.compile(r"[0-9A-Fa-f]{64,200}")

TITLE = "RingSays"
BODY = {"INTENT": "طلب اتصال جديد · New call request", "PRECALL": "مكالمة واردة الآن · Calling you now"}
ANDROID_CHANNEL = "intents"  # created by the app (apps/mobile/src/lib/push.ts)


def _data(kind: str, intent_id: UUID) -> dict[str, str]:
    return {"intent_id": str(intent_id), "kind": kind}


@dataclass
class FcmSender:
    """FCM HTTP v1. Access token from workload identity (role Firebase Cloud Messaging API Admin on the
    Firebase project); `token_provider` is injectable for tests."""

    project_id: str
    token_provider: Callable[[], str] | None = None
    client: httpx.Client | None = None
    base_url: str = "https://fcm.googleapis.com"
    _creds: object | None = field(default=None, repr=False)
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def _token(self) -> str:
        if self.token_provider is not None:
            return self.token_provider()
        import google.auth
        import google.auth.transport.requests

        with self._lock:
            if self._creds is None:
                self._creds, _ = google.auth.default(
                    scopes=["https://www.googleapis.com/auth/firebase.messaging"]
                )
            creds = self._creds
            if not creds.valid:  # type: ignore[attr-defined]
                creds.refresh(google.auth.transport.requests.Request())  # type: ignore[attr-defined]
            return str(creds.token)  # type: ignore[attr-defined]

    def send(self, target: PushTarget, kind: str, intent_id: UUID) -> PushResult:
        message = {
            "message": {
                "token": target.token,
                "data": _data(kind, intent_id),
                "notification": {"title": TITLE, "body": BODY.get(kind, BODY["INTENT"])},
                "android": {
                    "priority": "HIGH",
                    "ttl": "900s",
                    "notification": {"channel_id": ANDROID_CHANNEL, "tag": str(intent_id)},
                },
            }
        }
        try:
            bearer = self._token()
        except Exception as exc:  # google.auth refresh or transport error: report, never crash the tick
            return PushResult(ok=False, error=f"fcm credentials: {type(exc).__name__}")
        c = self.client or httpx.Client(timeout=TIMEOUT)
        try:
            r = c.post(
                f"{self.base_url}/v1/projects/{self.project_id}/messages:send",
                headers={"Authorization": f"Bearer {bearer}"},
                json=message,
            )
        except httpx.HTTPError as exc:
            return PushResult(ok=False, error=f"fcm unreachable: {type(exc).__name__}")
        finally:
            if self.client is None:
                c.close()
        if r.status_code == 200:
            return PushResult(ok=True)
        status = ""
        try:
            status = r.json().get("error", {}).get("status", "")
        except ValueError:
            pass
        # UNREGISTERED / NOT_FOUND / INVALID_ARGUMENT: the token is dead; delivery moves to the next channel.
        return PushResult(ok=False, error=f"fcm {r.status_code} {status}".strip())


@dataclass
class ApnsSender:
    """APNs with a token signing key (.p8): ES256 JWT reused for 40 minutes (Apple allows 20 to 60)."""

    key_id: str
    team_id: str
    private_key_pem: str
    topic: str  # app bundle id, com.peocit.ringsays
    sandbox: bool = False
    client: httpx.Client | None = None
    clock: Callable[[], float] = time.time
    _jwt: tuple[float, str] | None = field(default=None, repr=False)
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    @property
    def base_url(self) -> str:
        return "https://api.sandbox.push.apple.com" if self.sandbox else "https://api.push.apple.com"

    def _auth(self) -> str:
        with self._lock:
            now = self.clock()
            if self._jwt is None or now - self._jwt[0] > 40 * 60:
                token = jwt.encode(
                    {"iss": self.team_id, "iat": int(now)},
                    self.private_key_pem,
                    algorithm="ES256",
                    headers={"kid": self.key_id},
                )
                self._jwt = (now, token)
            return self._jwt[1]

    def _http(self) -> httpx.Client:
        return self.client or httpx.Client(http2=True, timeout=TIMEOUT)

    def send(self, target: PushTarget, kind: str, intent_id: UUID) -> PushResult:
        payload = {
            "aps": {
                "alert": {"title": TITLE, "body": BODY.get(kind, BODY["INTENT"])},
                "sound": "default",
                "thread-id": str(intent_id),
                "interruption-level": "time-sensitive" if kind == "PRECALL" else "active",
            },
            **_data(kind, intent_id),
        }
        if not _APNS_TOKEN.fullmatch(target.token):
            return PushResult(ok=False, error="apns token malformed")
        try:
            bearer = self._auth()
        except Exception as exc:  # unreadable key: install() checks at start, this is a last guard
            return PushResult(ok=False, error=f"apns key: {type(exc).__name__}")
        c = self._http()
        try:
            r = c.post(
                f"{self.base_url}/3/device/{target.token}",
                headers={
                    "authorization": f"bearer {bearer}",
                    "apns-topic": self.topic,
                    "apns-push-type": "alert",
                    "apns-priority": "10",
                    "apns-expiration": str(int(self.clock()) + 900),
                    "apns-collapse-id": str(intent_id)[:64],
                },
                json=payload,
            )
        except httpx.HTTPError as exc:
            return PushResult(ok=False, error=f"apns unreachable: {type(exc).__name__}")
        finally:
            if self.client is None:
                c.close()
        if r.status_code == 200:
            return PushResult(ok=True)
        reason = ""
        try:
            reason = r.json().get("reason", "")
        except ValueError:
            pass
        return PushResult(ok=False, error=f"apns {r.status_code} {reason}".strip())


@dataclass
class PlatformPushSender:
    """Routes by device platform. A platform without a configured provider fails that push only."""

    fcm: FcmSender | None
    apns: ApnsSender | None

    def send(self, target: PushTarget, kind: str, intent_id: UUID) -> PushResult:
        sender = self.apns if target.platform == "IOS" else self.fcm
        if sender is None:
            return PushResult(ok=False, error=f"no push provider for {target.platform}")
        return sender.send(target, kind, intent_id)
