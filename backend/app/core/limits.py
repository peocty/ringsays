"""ASGI middleware applied before routing, authentication or form parsing.

- Request bodies are capped (Content-Length checked up front, then counted while streaming), so an
  unauthenticated client cannot make the server read or spool a large upload.
- NUL bytes in the path or query string are refused with 400 (PostgreSQL text cannot hold them).
"""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable, MutableMapping
from typing import Any

Scope = MutableMapping[str, Any]
Message = MutableMapping[str, Any]
Receive = Callable[[], Awaitable[Message]]
Send = Callable[[Message], Awaitable[None]]
ASGIApp = Callable[[Scope, Receive, Send], Awaitable[None]]

DEFAULT_LIMIT = 1024 * 1024  # 1 MiB for JSON and forms
UPLOAD_LIMIT = 5 * 1024 * 1024 + 64 * 1024  # 5 MiB file plus multipart overhead
UPLOAD_SUFFIX = "/verification/documents"


async def _problem(send: Send, status: int, code: str, detail: str) -> None:
    body = json.dumps(
        {
            "type": "https://errors.ringsays.app/" + code.replace("_", "-"),
            "title": "Payload too large" if status == 413 else "Bad request",
            "status": status,
            "code": code,
            "detail": detail,
        }
    ).encode()
    await send(
        {
            "type": "http.response.start",
            "status": status,
            "headers": [
                (b"content-type", b"application/problem+json"),
                (b"content-length", str(len(body)).encode()),
            ],
        }
    )
    await send({"type": "http.response.body", "body": body})


class RequestLimits:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        raw_path: bytes = scope.get("raw_path") or scope.get("path", "").encode()
        query: bytes = scope.get("query_string", b"")
        if b"\x00" in raw_path or b"%00" in raw_path.lower() or b"%00" in query.lower() or b"\x00" in query:
            await _problem(send, 400, "validation_failed", "Request contains a NUL character")
            return
        limit = UPLOAD_LIMIT if scope.get("path", "").endswith(UPLOAD_SUFFIX) else DEFAULT_LIMIT
        for name, value in scope.get("headers", []):
            if name == b"content-length":
                try:
                    if int(value) > limit:
                        await _problem(
                            send, 413, "payload_too_large", f"Request body is limited to {limit} bytes"
                        )
                        return
                except ValueError:
                    await _problem(send, 400, "validation_failed", "Bad Content-Length")
                    return
        received = 0
        started = False
        refused = False

        async def counted_receive() -> Message:
            # Over the limit: answer 413 ourselves and tell the app the client went away. Raising
            # here would be turned into a generic 400 by the framework's body parser.
            nonlocal received, refused
            if refused:
                return {"type": "http.disconnect"}
            message = await receive()
            if message["type"] == "http.request":
                received += len(message.get("body", b""))
                if received > limit:
                    refused = True
                    if not started:
                        await _problem(
                            send, 413, "payload_too_large", f"Request body is limited to {limit} bytes"
                        )
                    return {"type": "http.disconnect"}
            return message

        async def guarded_send(message: Message) -> None:
            nonlocal started
            if refused:
                return  # response already sent
            if message["type"] == "http.response.start":
                started = True
            await send(message)

        try:
            await self.app(scope, counted_receive, guarded_send)
        except Exception:
            if not refused:
                raise
