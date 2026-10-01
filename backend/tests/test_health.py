from __future__ import annotations

from fastapi.testclient import TestClient

from app.main import app


def test_health_reports_mock_mode() -> None:
    body = TestClient(app).get("/health").json()
    assert body["status"] == "ok"
    assert body["mock_adapters"] is True
