"""FastAPI application entry point. Module routers are mounted here as they are built."""

from __future__ import annotations

import logging

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy import text

from app.core import auth, db, problems
from app.core.config import settings
from app.core.limits import RequestLimits
from app.core.proxy import TrustedProxyMiddleware
from app.modules.admin import api as admin_api
from app.modules.admin import backoffice_api
from app.modules.client import api as client_api
from app.modules.client.directory import DbRecipientDirectory
from app.modules.delivery import adapters
from app.modules.intent import api as intent_api
from app.platform import ratelimit
from app.platform.providers import wiring as providers

# Application logs (uvicorn configures only its own loggers). One line per event, no personal data.
logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s %(message)s")
settings.assert_safe_for_environment()
# Recipient directory is RingSays' own database (not an external integration), so it is always real.
adapters.configure(directory=DbRecipientDirectory())
providers.install()  # real SMS and push unless MOCK adapters

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
if settings.environment == "local" and settings.use_mock_adapters:
    from app.modules.devtools import api as devtools_api

    app.include_router(devtools_api.router)

# Portal runs on its own origin. Bearer tokens (never cookies), so no credentials mode is needed.
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.portal_origins,
    allow_methods=["GET", "POST", "PATCH", "PUT", "DELETE"],
    allow_headers=[
        "Authorization",
        "Content-Type",
        "Accept-Language",
        "If-Match",
        "Idempotency-Key",
        "RingSays-Device-Id",
    ],
    expose_headers=["ETag", "Content-Disposition"],
    max_age=600,
)
# Outermost: limits apply before CORS, routing, authentication and body parsing.
app.add_middleware(RequestLimits)
# Before anything reads the client address: real client behind trusted load balancers only.
app.add_middleware(TrustedProxyMiddleware, trusted=settings.trusted_proxies)


@app.get("/ready", tags=["Ops"], include_in_schema=False)
def ready() -> JSONResponse:
    """Readiness: this process can reach its database and Redis. Liveness stays /health (no I/O)."""
    checks: dict[str, str] = {}
    try:
        with db.get_engine().connect() as c:
            c.execute(text("SELECT 1"))
        checks["database"] = "ok"
    except Exception:
        checks["database"] = "unavailable"
    try:
        ratelimit.get_limiter().ping()
        checks["redis"] = "ok"
    except Exception:
        checks["redis"] = "unavailable"
    ok = all(v == "ok" for v in checks.values())
    return JSONResponse({"status": "ok" if ok else "unavailable", **checks}, status_code=200 if ok else 503)


@app.get("/health", tags=["Ops"])
def health() -> dict[str, object]:
    return {
        "status": "ok",
        "environment": settings.environment,
        "region": settings.residency_region,
        "mock_adapters": settings.use_mock_adapters,
    }
