"""FastAPI application entry point. Module routers are mounted here as they are built."""

from __future__ import annotations

from fastapi import FastAPI

from app.core import auth, problems
from app.core.config import settings
from app.modules.intent import api as intent_api

settings.assert_safe_for_environment()

app = FastAPI(title="RingSays API", version="0.1.0")
problems.install(app)
app.include_router(auth.router)
app.include_router(intent_api.router)


@app.get("/health", tags=["Ops"])
def health() -> dict[str, object]:
    return {
        "status": "ok",
        "environment": settings.environment,
        "region": settings.residency_region,
        "mock_adapters": settings.use_mock_adapters,
    }
