from __future__ import annotations

from fastapi.testclient import TestClient

from app.main import app


def test_health_reports_mock_mode() -> None:
    body = TestClient(app).get("/health").json()
    assert body["status"] == "ok"
    assert body["mock_adapters"] is True


def test_ready_reports_dependencies(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    from fastapi.testclient import TestClient

    from app.core import db
    from app.main import app
    from app.platform import ratelimit

    class Down:
        def connect(self) -> None:
            raise OSError("down")

    class Limiter:
        def ping(self) -> None:
            return None

    monkeypatch.setattr(db, "get_engine", lambda: Down())
    monkeypatch.setattr(ratelimit, "get_limiter", lambda: Limiter())
    r = TestClient(app).get("/ready")
    assert r.status_code == 503
    assert r.json() == {"status": "unavailable", "database": "unavailable", "redis": "ok"}
