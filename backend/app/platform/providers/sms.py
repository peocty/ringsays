"""SMS providers licensed in the Kingdom (CST registered sender IDs). Used for sign in codes only.

Neither the phone number nor the message is ever logged. A provider failure raises SmsSendFailed;
the API answers 503 and the person can ask for a new code.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

import httpx

log = logging.getLogger(__name__)
TIMEOUT = httpx.Timeout(5.0, connect=3.0)


def _json(r: httpx.Response) -> dict[str, object]:
    try:
        body = r.json()
    except ValueError:
        return {}
    return body if isinstance(body, dict) else {}


class SmsSendFailed(Exception):
    """Provider refused or could not be reached. Message is safe to log (no number, no text)."""


def _digits(phone_e164: str) -> str:
    """Both providers want international format without + or 00 (966500000000)."""
    if not phone_e164.startswith("+") or not phone_e164[1:].isdigit():
        raise SmsSendFailed("phone must be E.164")
    return phone_e164[1:]


@dataclass
class TaqnyatSms:
    """Taqnyat: POST https://api.taqnyat.sa/v1/messages, bearer token, 201 on success."""

    token: str
    sender: str
    client: httpx.Client | None = None
    base_url: str = "https://api.taqnyat.sa"

    def send(self, phone_e164: str, message: str) -> None:
        c = self.client or httpx.Client(timeout=TIMEOUT)
        try:
            r = c.post(
                f"{self.base_url}/v1/messages",
                headers={"Authorization": f"Bearer {self.token}"},
                json={"recipients": [_digits(phone_e164)], "body": message, "sender": self.sender},
            )
        except httpx.HTTPError as exc:
            raise SmsSendFailed(f"taqnyat unreachable: {type(exc).__name__}") from exc
        finally:
            if self.client is None:
                c.close()
        if r.status_code != 201:
            raise SmsSendFailed(f"taqnyat refused: HTTP {r.status_code}")
        body = _json(r)  # 201 is acceptance; an odd body (proxy page) is not a reason to resend
        if "[]" == str(body.get("accepted", "")).replace(" ", ""):
            raise SmsSendFailed("taqnyat accepted no recipient")
        log.info("sms sent provider=taqnyat message_id=%s", body.get("messageId"))


@dataclass
class UnifonicSms:
    """Unifonic: POST https://el.cloud.unifonic.com/rest/SMS/messages, form encoded, AppSid."""

    app_sid: str
    sender: str
    client: httpx.Client | None = None
    base_url: str = "https://el.cloud.unifonic.com"

    def send(self, phone_e164: str, message: str) -> None:
        c = self.client or httpx.Client(timeout=TIMEOUT)
        try:
            r = c.post(
                f"{self.base_url}/rest/SMS/messages",
                data={
                    "AppSid": self.app_sid,
                    "SenderID": self.sender,
                    "Recipient": _digits(phone_e164),
                    "Body": message,
                    "responseType": "JSON",
                    "async": "false",
                },
            )
        except httpx.HTTPError as exc:
            raise SmsSendFailed(f"unifonic unreachable: {type(exc).__name__}") from exc
        finally:
            if self.client is None:
                c.close()
        body = _json(r)
        if not body:
            raise SmsSendFailed(f"unifonic bad response: HTTP {r.status_code}")
        if r.status_code != 200 or body.get("success") is not True:
            raise SmsSendFailed(f"unifonic refused: {body.get('errorCode', r.status_code)}")
        data = body.get("data")
        log.info(
            "sms sent provider=unifonic message_id=%s",
            data.get("MessageID") if isinstance(data, dict) else None,
        )
