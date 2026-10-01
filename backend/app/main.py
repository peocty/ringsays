"""FastAPI application entry point. Module routers are mounted here as they are built."""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.core import auth, problems
from app.core.config import settings
from app.modules.admin import api as admin_api
from app.modules.admin import backoffice_api
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
app.include_router(admin_api.router)
if settings.backoffice_enabled:
    app.include_router(backoffice_api.router)
if settings.dev_oidc_enabled and settings.environment == "local":
    from app.modules.devoidc import api as devoidc_api

    app.include_router(devoidc_api.router)

# Portal runs on its own origin. Bearer tokens (never cookies), so no credentials mode is needed.
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.portal_origins,
    allow_methods=["GET", "POST", "PATCH", "PUT", "DELETE"],
    allow_headers=["Authorization", "Content-Type", "Accept-Language", "If-Match", "Idempotency-Key"],
    expose_headers=["ETag", "Content-Disposition"],
    max_age=600,
)


@app.get("/health", tags=["Ops"])
def health() -> dict[str, object]:
    return {
        "status": "ok",
        "environment": settings.environment,
        "region": settings.residency_region,
        "mock_adapters": settings.use_mock_adapters,
    }
