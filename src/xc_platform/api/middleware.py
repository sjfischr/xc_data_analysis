"""Cross-cutting middleware (Task 13.1): request IDs and security headers
on every response.
"""

from __future__ import annotations

import uuid
from collections.abc import Awaitable, Callable

from fastapi import FastAPI, Request, Response

SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "same-origin",
    # No inline scripts, no remote origins beyond self -- the analytics
    # agent never returns executable content (Requirement 10.8) and the
    # chart contract is inline data only (design.md 11.4), so a strict
    # policy costs nothing here.
    "Content-Security-Policy": "default-src 'self'; frame-ancestors 'none'",
}


def install_middleware(app: FastAPI) -> None:
    @app.middleware("http")
    async def add_request_id_and_security_headers(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        request_id = request.headers.get("x-request-id") or str(uuid.uuid4())
        request.state.request_id = request_id
        response = await call_next(request)
        response.headers["X-Request-Id"] = request_id
        for name, value in SECURITY_HEADERS.items():
            response.headers[name] = value
        return response
