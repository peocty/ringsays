"""Local environment only: webhooks to a receiver on this machine (mock bank). Never outside local."""

from __future__ import annotations

import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Any

import pytest

from app.core.config import settings
from app.modules.intent.errors import RuleViolation
from app.modules.webhooks import service as webhooks

pytestmark = pytest.mark.integration


def test_loopback_url_allowed_only_in_local(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "environment", "local")
    webhooks.validate_url("http://127.0.0.1:4100/webhooks/ringsays")
    webhooks.validate_url("http://localhost:4100/hook")
    with pytest.raises(RuleViolation):
        webhooks.validate_url("http://user:pw@127.0.0.1:4100/hook")
    with pytest.raises(RuleViolation):
        webhooks.validate_url("http://10.0.0.5/hook")  # private, not loopback
    monkeypatch.setattr(settings, "environment", "production")
    with pytest.raises(RuleViolation):
        webhooks.validate_url("http://127.0.0.1:4100/webhooks/ringsays")


def _receiver() -> tuple[HTTPServer, list[dict[str, Any]]]:
    got: list[dict[str, Any]] = []

    class H(BaseHTTPRequestHandler):
        def do_POST(self) -> None:
            body = self.rfile.read(int(self.headers["Content-Length"]))
            got.append({"path": self.path, "body": body, "sig": self.headers.get("RingSays-Signature")})
            self.send_response(204)
            self.end_headers()

        def log_message(self, *a: object) -> None:
            pass

    srv = HTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, got


def test_loopback_sender_posts_locally_and_routes_others(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "environment", "local")
    srv, got = _receiver()
    other = webhooks.MockHttpSender()
    sender = webhooks.LoopbackHttpSender(other)
    url = f"http://127.0.0.1:{srv.server_port}/webhooks/ringsays"
    r = sender.post(url, b'{"a":1}', {"RingSays-Signature": "t=1,v1=x", "Content-Type": "application/json"})
    assert r.status == 204
    assert got[0]["path"] == "/webhooks/ringsays" and got[0]["sig"] == "t=1,v1=x"
    assert sender.post("https://bank.example/hook", b"{}", {}).status == 200
    assert other.requests[0][0] == "https://bank.example/hook"

    monkeypatch.setattr(settings, "environment", "production")
    refused = sender.post(url, b"{}", {})
    assert refused.status is None and refused.error and "local only" in refused.error
    srv.shutdown()


def test_loopback_needs_path_and_no_ipv6(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "environment", "local")
    with pytest.raises(RuleViolation):
        webhooks.validate_url("http://127.0.0.1:4100")
    with pytest.raises(RuleViolation):
        webhooks.validate_url("http://[::1]:4100/hook")


def test_http_origins_refused_outside_local() -> None:
    from app.core.config import Settings

    s = Settings(
        environment="staging",
        jwt_secret="x" * 40,
        webhook_secret_key="Zm9vYmFyYmF6cXV4cXV1eHF1dXhxdXV4cXV1eHF1dXg=",  # noqa: S106
        phone_pepper="p",
        admin_oidc_issuer="https://idp.example",
        staff_oidc_issuer="https://staff.example",
        backoffice_enabled=False,
        use_mock_adapters=False,
    )
    with pytest.raises(RuntimeError, match="PORTAL_ORIGINS"):
        s.assert_safe_for_environment()
    s.portal_origins = ["https://portal.bank.example"]
    s.assert_safe_for_environment()
