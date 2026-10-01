"""FastAPI application entry point. Module routers are mounted here as they are built."""

from __future__ import annotations

from fastapi import FastAPI

from app.core.config import settings

app = FastAPI(title="RingSays API", version="0.1.0")


@app.get("/health", tags=["Ops"])
def health() -> dict[str, object]:
    return {
        "status": "ok",
        "environment": settings.environment,
        "region": settings.residency_region,
        "mock_adapters": settings.use_mock_adapters,
    }
