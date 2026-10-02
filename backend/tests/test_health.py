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


def test_settings_read_secret_files(tmp_path, monkeypatch) -> None:  # type: ignore[no-untyped-def]

    from app.core import config

    (tmp_path / "RINGSAYS_JWT_SECRET").write_text("from-a-file-0123456789")
    (tmp_path / "RINGSAYS_DATABASE_URL").write_text("postgresql+psycopg://ringsays_app:pw@db:5432/ringsays\n")
    monkeypatch.setenv("RINGSAYS_SETTINGS_DIR", str(tmp_path))
    monkeypatch.delenv("RINGSAYS_JWT_SECRET", raising=False)
    monkeypatch.delenv("RINGSAYS_DATABASE_URL", raising=False)
    loaded = config._load()
    assert loaded.jwt_secret == "from-a-file-0123456789"  # noqa: S105 (test value)
    assert loaded.database_url == "postgresql+psycopg://ringsays_app:pw@db:5432/ringsays"
