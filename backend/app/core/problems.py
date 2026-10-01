"""RFC 9457 problem details for every error response."""

from __future__ import annotations

import logging
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from sqlalchemy.exc import DataError, DBAPIError

from app.modules.intent.errors import IntentError

log = logging.getLogger("ringsays.errors")
BASE = "https://errors.ringsays.app/"
TITLES = {
    400: "Bad request",
    401: "Unauthorized",
    403: "Forbidden",
    404: "Not found",
    409: "Conflict",
    410: "Gone",
    412: "Precondition failed",
    422: "Unprocessable entity",
    429: "Too many requests",
    500: "Internal server error",
}


def problem(
    status: int,
    code: str,
    detail: str,
    extra: dict[str, Any] | None = None,
    headers: dict[str, str] | None = None,
) -> JSONResponse:
    body = {
        "type": BASE + code.replace("_", "-"),
        "title": TITLES.get(status, "Error"),
        "status": status,
        "code": code,
        "detail": detail,
        **(extra or {}),
    }
    return JSONResponse(body, status_code=status, media_type="application/problem+json", headers=headers)


def install(app: FastAPI) -> None:
    @app.exception_handler(IntentError)
    async def _intent_error(_: Request, exc: IntentError) -> JSONResponse:
        retry = getattr(exc, "retry_after_s", None)
        headers = {"Retry-After": str(retry)} if retry is not None else None
        return problem(exc.http_status, exc.code, exc.detail, headers=headers)

    @app.exception_handler(RequestValidationError)
    async def _validation(_: Request, exc: RequestValidationError) -> JSONResponse:
        errors = [
            {"field": ".".join(str(p) for p in e["loc"][1:]) or str(e["loc"][0]), "message": e["msg"]}
            for e in exc.errors()
        ]
        return problem(400, "validation_failed", "Request failed validation", {"errors": errors})

    @app.exception_handler(DBAPIError)
    async def _database(_: Request, exc: DBAPIError) -> JSONResponse:
        sqlstate = getattr(exc.orig, "sqlstate", None)
        if sqlstate == "RSG01":
            # A database guard refused a change (for example evidence locked by a concurrent submit).
            log.warning("database guard refused a change")
            return problem(409, "state_changed", "This changed while you were working; reload and try again")
        if isinstance(exc, DataError) or (sqlstate and sqlstate.startswith("22")):
            return problem(400, "validation_failed", "A value is not valid")
        log.error("database error: %s %s", type(exc).__name__, sqlstate)
        return problem(500, "internal_error", "Unexpected error; it has been logged")

    @app.exception_handler(Exception)
    async def _unexpected(_: Request, exc: Exception) -> JSONResponse:
        if isinstance(exc, ValueError) and "NUL" in str(exc):
            return problem(400, "validation_failed", "Request contains a NUL character")
        # Log type only: messages from drivers can contain request data.
        log.error("unhandled error: %s", type(exc).__name__)
        return problem(500, "internal_error", "Unexpected error; it has been logged")

    @app.exception_handler(HTTPException)
    async def _http(_: Request, exc: HTTPException) -> JSONResponse:
        detail = str(exc.detail)
        code = detail if detail.replace("_", "").isalpha() and detail.islower() else f"http_{exc.status_code}"
        return problem(exc.status_code, code, detail, headers=getattr(exc, "headers", None))
