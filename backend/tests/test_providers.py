# ruff: noqa: S105, S106  (push tokens and fake keys in tests are not secrets)
"""Real SMS and push adapters against recorded provider behaviour (httpx mock transport, no network)."""

from __future__ import annotations

import json
from urllib.parse import parse_qs
from uuid import UUID, uuid4

import httpx
import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec

from app.modules.delivery.adapters import PushResult, PushTarget
from app.platform.providers.push import ApnsSender, FcmSender, PlatformPushSender
from app.platform.providers.sms import SmsSendFailed, TaqnyatSms, UnifonicSms

PHONE = "+966500000123"
INTENT = UUID("01a0f935-d553-7072-8d32-a4940fc4b991")


def client(handler):  # type: ignore[no-untyped-def]
    seen: list[httpx.Request] = []

    def wrapped(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return handler(request)

    return httpx.Client(transport=httpx.MockTransport(wrapped)), seen


# SMS


def test_taqnyat_request_and_success() -> None:
    c, seen = client(
        lambda r: httpx.Response(
            201,
            json={"statusCode": 201, "messageId": 5452899970, "accepted": "[966500000123]", "rejected": "[]"},
        )
    )
    TaqnyatSms(token="tok", sender="RingSays", client=c).send(PHONE, "رمز RingSays: 123456")
    r = seen[0]
    assert str(r.url) == "https://api.taqnyat.sa/v1/messages"
    assert r.headers["authorization"] == "Bearer tok"
    assert json.loads(r.content) == {
        "recipients": ["966500000123"],
        "body": "رمز RingSays: 123456",
        "sender": "RingSays",
    }


@pytest.mark.parametrize(
    "response",
    [
        httpx.Response(400, json={"statusCode": 400, "message": "Sender Name not active"}),
        httpx.Response(401, json={"statusCode": 401}),
        httpx.Response(201, json={"statusCode": 201, "accepted": "[]", "rejected": "[966500000123]"}),
    ],
)
def test_taqnyat_failures_raise(response: httpx.Response) -> None:
    c, _ = client(lambda r: response)
    with pytest.raises(SmsSendFailed) as e:
        TaqnyatSms(token="tok", sender="RingSays", client=c).send(PHONE, "x")
    assert "966500000123" not in str(e.value)  # no number in errors (they reach logs)


def test_unifonic_request_and_failure() -> None:
    c, seen = client(
        lambda r: httpx.Response(
            200, json={"success": True, "errorCode": "ER-00", "data": {"MessageID": 42, "Status": "Sent"}}
        )
    )
    UnifonicSms(app_sid="sid", sender="RingSays", client=c).send(PHONE, "RingSays code: 123456")
    r = seen[0]
    assert str(r.url) == "https://el.cloud.unifonic.com/rest/SMS/messages"
    form = {k: v[0] for k, v in parse_qs(r.content.decode()).items()}
    assert form == {
        "AppSid": "sid",
        "SenderID": "RingSays",
        "Recipient": "966500000123",
        "Body": "RingSays code: 123456",
        "responseType": "JSON",
        "async": "false",
    }
    bad, _ = client(
        lambda r: httpx.Response(
            200, json={"success": False, "errorCode": "ER-440", "message": "Wrong sender"}
        )
    )
    with pytest.raises(SmsSendFailed, match="ER-440"):
        UnifonicSms(app_sid="sid", sender="RingSays", client=bad).send(PHONE, "x")


def test_unreachable_provider_raises() -> None:
    def boom(r: httpx.Request) -> httpx.Response:
        raise httpx.ConnectTimeout("timeout")

    c, _ = client(boom)
    with pytest.raises(SmsSendFailed, match="unreachable"):
        TaqnyatSms(token="t", sender="s", client=c).send(PHONE, "x")


# Push


def test_fcm_message_carries_only_ids() -> None:
    c, seen = client(lambda r: httpx.Response(200, json={"name": "projects/p/messages/1"}))
    target = PushTarget(device_id=uuid4(), platform="ANDROID", token="fcm-token")
    res = FcmSender(project_id="ringsays-prod", token_provider=lambda: "oauth", client=c).send(
        target, "INTENT", INTENT
    )
    assert res.ok
    r = seen[0]
    assert str(r.url) == "https://fcm.googleapis.com/v1/projects/ringsays-prod/messages:send"
    assert r.headers["authorization"] == "Bearer oauth"
    msg = json.loads(r.content)["message"]
    assert msg["token"] == "fcm-token"
    assert msg["data"] == {"intent_id": str(INTENT), "kind": "INTENT"}
    assert msg["android"]["notification"]["channel_id"] == "intents"
    assert msg["android"]["priority"] == "HIGH"


def test_fcm_dead_token_is_a_failed_push() -> None:
    c, _ = client(
        lambda r: httpx.Response(
            404, json={"error": {"status": "NOT_FOUND", "details": [{"errorCode": "UNREGISTERED"}]}}
        )
    )
    res = FcmSender(project_id="p", token_provider=lambda: "o", client=c).send(
        PushTarget(device_id=uuid4(), platform="ANDROID", token="dead"), "INTENT", INTENT
    )
    assert not res.ok and res.error == "fcm 404 NOT_FOUND"


def _p8() -> tuple[str, ec.EllipticCurvePublicKey]:
    key = ec.generate_private_key(ec.SECP256R1())
    pem = key.private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()
    )
    return pem.decode(), key.public_key()


def test_apns_request_jwt_and_reuse() -> None:
    pem, public = _p8()
    c, seen = client(lambda r: httpx.Response(200))
    now = [1_790_000_000.0]
    apns = ApnsSender(
        key_id="ABC123DEFG",
        team_id="TEAM123456",
        private_key_pem=pem,
        topic="com.peocit.ringsays",
        client=c,
        clock=lambda: now[0],
    )
    target = PushTarget(device_id=uuid4(), platform="IOS", token="a" * 64)
    assert apns.send(target, "PRECALL", INTENT).ok
    r = seen[0]
    assert str(r.url) == f"https://api.push.apple.com/3/device/{'a' * 64}"
    assert r.headers["apns-topic"] == "com.peocit.ringsays"
    assert r.headers["apns-push-type"] == "alert" and r.headers["apns-priority"] == "10"
    token = r.headers["authorization"].removeprefix("bearer ")
    assert jwt.get_unverified_header(token) == {"alg": "ES256", "kid": "ABC123DEFG", "typ": "JWT"}
    assert jwt.decode(token, public, algorithms=["ES256"]) == {"iss": "TEAM123456", "iat": 1_790_000_000}
    body = json.loads(r.content)
    assert body["intent_id"] == str(INTENT) and body["kind"] == "PRECALL"
    assert body["aps"]["interruption-level"] == "time-sensitive"
    # Same token within 40 minutes, a new one after.
    now[0] += 30 * 60
    apns.send(target, "INTENT", INTENT)
    assert seen[1].headers["authorization"] == r.headers["authorization"]
    now[0] += 11 * 60
    apns.send(target, "INTENT", INTENT)
    assert seen[2].headers["authorization"] != r.headers["authorization"]


def test_apns_unregistered_and_sandbox() -> None:
    pem, _ = _p8()
    c, seen = client(lambda r: httpx.Response(410, json={"reason": "Unregistered", "timestamp": 1}))
    apns = ApnsSender(key_id="K", team_id="T", private_key_pem=pem, topic="t", sandbox=True, client=c)
    res = apns.send(PushTarget(device_id=uuid4(), platform="IOS", token="b" * 64), "INTENT", INTENT)
    assert not res.ok and res.error == "apns 410 Unregistered"
    assert str(seen[0].url).startswith("https://api.sandbox.push.apple.com/")


def test_platform_routing_and_missing_provider() -> None:
    c, seen = client(lambda r: httpx.Response(200))
    sender = PlatformPushSender(
        fcm=FcmSender(project_id="p", token_provider=lambda: "o", client=c), apns=None
    )
    assert sender.send(PushTarget(device_id=uuid4(), platform="ANDROID", token="t"), "INTENT", INTENT).ok
    ios = sender.send(PushTarget(device_id=uuid4(), platform="IOS", token="t"), "INTENT", INTENT)
    assert not ios.ok and "IOS" in (ios.error or "")
    assert len(seen) == 1


# Settings


def test_real_providers_must_be_configured() -> None:
    from app.core.config import Settings

    s = Settings(
        environment="production",
        jwt_secret="x" * 40,
        webhook_secret_key="Zm9vYmFyYmF6cXV4cXV1eHF1dXhxdXV4cXV1eHF1dXg=",
        phone_pepper="p",
        admin_oidc_issuer="https://idp.example",
        staff_oidc_issuer="https://staff.example",
        backoffice_enabled=False,
        use_mock_adapters=False,
        blob_backend="gcs",
        blob_bucket="b",
        portal_origins=["https://portal.ringsays.sa"],
    )
    with pytest.raises(RuntimeError, match="SMS_PROVIDER") as e:
        s.assert_safe_for_environment()
    assert "FCM_PROJECT_ID" in str(e.value) and "APNS" in str(e.value)
    s.sms_provider, s.sms_sender_id, s.sms_api_key = "taqnyat", "RingSays", "k"
    s.fcm_project_id, s.apns_key_id, s.apns_team_id, s.apns_private_key = "p", "k", "t", "pem"
    s.assert_safe_for_environment()


def test_fcm_credential_failure_is_a_failed_push_not_a_crash() -> None:
    def broken() -> str:
        raise OSError("metadata server unreachable")

    c = httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(200, json={})))
    res = FcmSender(project_id="p", token_provider=broken, client=c).send(
        PushTarget(device_id=uuid4(), platform="ANDROID", token="t"), "INTENT", INTENT
    )
    assert not res.ok and res.error == "fcm credentials: OSError"


def test_wiring_shares_one_http2_client_for_apple(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.core.config import settings
    from app.platform.providers import wiring

    monkeypatch.setattr(settings, "fcm_project_id", "p")
    monkeypatch.setattr(settings, "apns_key_id", "K")
    monkeypatch.setattr(settings, "apns_team_id", "T")
    monkeypatch.setattr(settings, "apns_private_key", "pem")
    sender = wiring.push_from_settings()
    assert sender.apns is not None and sender.apns.client is not None
    assert sender.fcm is not None and sender.fcm.client is not None
    assert sender.apns._http() is sender.apns.client


def test_apns_refuses_a_token_that_is_not_hex() -> None:
    seen: list[httpx.Request] = []
    c = httpx.Client(transport=httpx.MockTransport(lambda r: seen.append(r) or httpx.Response(200)))
    apns = ApnsSender(key_id="K", team_id="T", private_key_pem=_p8()[0], topic="t", client=c)
    res = apns.send(PushTarget(device_id=uuid4(), platform="IOS", token="../../x?y"), "INTENT", INTENT)
    assert not res.ok and res.error == "apns token malformed" and not seen


def test_install_fails_fast_on_an_unreadable_apple_key(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.core.config import settings
    from app.platform.providers import wiring

    for k, v in {
        "use_mock_adapters": False,
        "backoffice_enabled": False,
        "sms_provider": "taqnyat",
        "fcm_project_id": "",
        "apns_key_id": "K",
        "apns_team_id": "T",
        "apns_private_key": "not a key",
    }.items():
        monkeypatch.setattr(settings, k, v)
    with pytest.raises(Exception):  # noqa: B017
        wiring.install()


def test_back_office_needs_no_provider_settings() -> None:
    from app.core.config import Settings

    s = Settings(
        environment="production",
        jwt_secret="x" * 40,
        webhook_secret_key="Zm9vYmFyYmF6cXV4cXV1eHF1dXhxdXV4cXV1eHF1dXg=",
        phone_pepper="p",
        admin_oidc_issuer="https://idp.example",
        staff_oidc_issuer="https://staff.example",
        use_mock_adapters=False,
        blob_backend="gcs",
        blob_bucket="b",
        backoffice_enabled=True,
        backoffice_database_url="postgresql+psycopg://ringsays_backoffice:real@db/ringsays",
        portal_origins=["https://portal.example"],
    )
    s.assert_safe_for_environment()  # no SMS or Apple values: fine for the back office


def test_back_office_install_is_a_no_op(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.core.config import settings
    from app.platform.providers import wiring

    monkeypatch.setattr(settings, "use_mock_adapters", False)
    monkeypatch.setattr(settings, "backoffice_enabled", True)
    monkeypatch.setattr(settings, "sms_provider", "nonsense")
    wiring.install()  # would raise for the unknown provider if it ran


def test_apns_payload_carries_ids_where_expo_reads_them() -> None:
    sent: list[httpx.Request] = []
    pem, _ = _p8()

    def handler(r: httpx.Request) -> httpx.Response:
        sent.append(r)
        return httpx.Response(200)

    c = httpx.Client(transport=httpx.MockTransport(handler))
    apns = ApnsSender(key_id="K", team_id="T", private_key_pem=pem, topic="t", client=c)
    assert apns.send(PushTarget(device_id=uuid4(), platform="IOS", token="a" * 64), "INTENT", INTENT).ok
    body = json.loads(sent[0].content)
    assert body["body"] == {"intent_id": str(INTENT), "kind": "INTENT"}
    assert body["intent_id"] == str(INTENT)


def test_apns_stale_provider_token_is_resigned_once() -> None:
    auths: list[str] = []
    now = [1_000_000.0]
    pem, _ = _p8()

    def handler(r: httpx.Request) -> httpx.Response:
        auths.append(r.headers["authorization"])
        if len(auths) == 1:
            return httpx.Response(403, json={"reason": "ExpiredProviderToken"})
        return httpx.Response(200)

    c = httpx.Client(transport=httpx.MockTransport(handler))
    apns = ApnsSender(key_id="K", team_id="T", private_key_pem=pem, topic="t", client=c, clock=lambda: now[0])
    apns._auth()
    now[0] += 5  # new iat, so a new token
    assert apns.send(PushTarget(device_id=uuid4(), platform="IOS", token="a" * 64), "INTENT", INTENT).ok
    assert len(auths) == 2 and auths[0] != auths[1]


def test_dead_token_flags() -> None:
    def fcm(status: int, body: object) -> PushResult:
        c = httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(status, json=body)))
        return FcmSender(project_id="p", token_provider=lambda: "o", client=c).send(
            PushTarget(device_id=uuid4(), platform="ANDROID", token="t"), "INTENT", INTENT
        )

    assert fcm(404, {"error": {"status": "UNREGISTERED"}}).dead_token
    assert not fcm(400, {"error": {"status": "INVALID_ARGUMENT"}}).dead_token
    assert not fcm(503, ["not", "a", "dict"]).dead_token  # odd body: failure, no exception

    pem, _ = _p8()

    def apns(status: int, reason: str) -> PushResult:
        c = httpx.Client(
            transport=httpx.MockTransport(lambda r: httpx.Response(status, json={"reason": reason}))
        )
        s = ApnsSender(key_id="K", team_id="T", private_key_pem=pem, topic="t", client=c)
        return s.send(PushTarget(device_id=uuid4(), platform="IOS", token="a" * 64), "INTENT", INTENT)

    assert apns(410, "Unregistered").dead_token
    assert apns(400, "BadDeviceToken").dead_token
    assert not apns(400, "DeviceTokenNotForTopic").dead_token  # our configuration, not the phone


def test_odd_provider_bodies_never_escape_as_other_errors() -> None:
    ok = httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(201, text="<html>ok</html>")))
    TaqnyatSms(token="t", sender="S", client=ok).send("+966500000001", "m")  # 201 is acceptance
    lst = httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(200, json=[1])))
    with pytest.raises(SmsSendFailed):
        UnifonicSms(app_sid="a", sender="S", client=lst).send("+966500000001", "m")
