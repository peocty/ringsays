"""Client address behind load balancers: only trusted proxies' X-Forwarded-For counts."""

from __future__ import annotations

from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from app.core.proxy import TrustedProxyMiddleware, client_from, parse_networks

LB = parse_networks(["10.129.0.0/23"])  # proxy only subnet


def test_rightmost_untrusted_hop_is_the_client() -> None:
    assert client_from("10.129.0.7", "203.0.113.9", LB) == "203.0.113.9"
    # Client tried to forge an address on the left: ignored.
    assert client_from("10.129.0.7", "1.2.3.4, 203.0.113.9", LB) == "203.0.113.9"
    # Two trusted hops.
    assert client_from("10.129.0.7", "198.51.100.4, 10.129.0.9", LB) == "198.51.100.4"


def test_header_ignored_unless_peer_is_trusted() -> None:
    assert client_from("198.51.100.4", "1.2.3.4", LB) == "198.51.100.4"
    assert client_from("10.129.0.7", "1.2.3.4", []) == "10.129.0.7"
    assert client_from("10.129.0.7", None, LB) == "10.129.0.7"
    assert client_from("10.129.0.7", "not-an-ip", LB) == "10.129.0.7"
    assert client_from("10.129.0.7", "2001:db8::1", LB) == "2001:db8::1"


def test_middleware_sets_request_client() -> None:
    app = FastAPI()

    @app.get("/ip")
    def ip(request: Request) -> dict[str, str]:
        return {"ip": request.client.host if request.client else ""}

    app.add_middleware(TrustedProxyMiddleware, trusted=["10.129.0.0/23"])
    behind_lb = TestClient(app, client=("10.129.0.7", 5000))
    assert (
        behind_lb.get("/ip", headers={"X-Forwarded-For": "1.2.3.4, 203.0.113.9"}).json()["ip"]
        == "203.0.113.9"
    )
    direct = TestClient(app, client=("198.51.100.4", 5000))
    assert direct.get("/ip", headers={"X-Forwarded-For": "1.2.3.4"}).json()["ip"] == "198.51.100.4"
