"""FastAPI application entry point. Module routers are mounted here as they are built."""

from __future__ import annotations

from fastapi import FastAPI

from app.core import auth, problems
from app.core.config import settings
from app.modules.client import api as client_api
from app.modules.client.directory import DbRecipientDirectory
from app.modules.delivery import adapters
from app.modules.intent import api as intent_api

settings.assert_safe_for_environment()
# Recipient directory is RingSays' own database (not an external integration), so it is always real.
adapters.configure(directory=DbRecipientDirectory())

app = FastAPI(title="RingSays API", version="0.1.0")
problems.install(app)
app.include_router(auth.router)
app.include_router(intent_api.router)
app.include_router(client_api.router)


@app.get("/health", tags=["Ops"])
def health() -> dict[str, object]:
    return {
        "status": "ok",
        "environment": settings.environment,
        "region": settings.residency_region,
        "mock_adapters": settings.use_mock_adapters,
    }
