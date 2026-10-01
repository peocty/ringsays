"""SDK in a browser preview (mock bank app web build) needs the device id header through CORS."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

pytestmark = pytest.mark.integration


def test_preflight_allows_device_id_header(client: TestClient) -> None:
    r = client.options(
        "/v1/tokens/abcdefghijklmnopqrstuv",
        headers={
            "Origin": "http://127.0.0.1:8082",
            "Access-Control-Request-Method": "GET",
            "Access-Control-Request-Headers": "ringsays-device-id,accept-language",
        },
    )
    assert r.status_code == 200
    assert r.headers["access-control-allow-origin"] == "http://127.0.0.1:8082"
    assert "ringsays-device-id" in r.headers["access-control-allow-headers"].lower()
