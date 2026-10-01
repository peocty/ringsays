"""Local MOCK helpers exist only in local environment with MOCK adapters."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

pytestmark = pytest.mark.integration


def test_mock_sms_code_readable_locally(client: TestClient) -> None:
    phone = "+966500009911"
    assert client.get("/dev/sms/last-code", params={"phone": phone}).status_code == 404
    client.post("/v1/auth/otp", json={"phone": phone, "locale": "en"})
    r = client.get("/dev/sms/last-code", params={"phone": phone})
    assert r.status_code == 200 and len(r.json()["code"]) == 6
