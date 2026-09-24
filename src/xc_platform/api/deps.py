"""FastAPI dependencies for context access and session/role enforcement
(Task 13.1/13.4).

``require_session``/``require_admin`` are the server-side enforcement
Requirement 13.3 demands ("enforce administrator and viewer permissions on
the server for every protected operation rather than relying on hidden UI
elements") -- every protected route depends on one of these, never on a
client-supplied role claim.

``require_session`` also enforces CSRF protection (Requirement 13.4) via
the standard double-submit pattern: the CSRF cookie is readable by
JavaScript (unlike the session cookie) specifically so the frontend can
echo it back as an ``X-CSRF-Token`` header, which only same-origin script
can do -- a cross-site form post can send the session cookie automatically
but cannot read the CSRF cookie to put its value in a custom header.
Enforced only for mutating methods; GET/HEAD/OPTIONS are exempt (standard
CSRF practice -- safe methods must not have side effects to protect).
"""

from __future__ import annotations

from collections.abc import Iterator

from fastapi import Depends, Request

from xc_platform.api.context import AppContext
from xc_platform.api.errors import ForbiddenError, UnauthenticatedError
from xc_platform.security.session import (
    CSRF_COOKIE_NAME,
    CSRF_HEADER_NAME,
    SESSION_COOKIE_NAME,
    SessionError,
    SessionRecord,
    require_valid_session,
)

_SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})


def get_context(request: Request) -> AppContext:
    ctx: AppContext | None = getattr(request.app.state, "ctx", None)
    if ctx is None:
        raise RuntimeError("AppContext not installed on app.state.ctx")
    return ctx


def writer_access(ctx: AppContext = Depends(get_context)) -> Iterator[None]:
    """Hold the writer lock for the whole request (see AppContext)."""
    with ctx.writer_lock:
        yield


def require_session(
    request: Request, ctx: AppContext = Depends(get_context)
) -> SessionRecord:
    session_id = request.cookies.get(SESSION_COOKIE_NAME)
    try:
        session = require_valid_session(ctx.session_store, session_id)
    except SessionError as error:
        raise UnauthenticatedError(str(error)) from error

    if request.method not in _SAFE_METHODS:
        header_token = request.headers.get(CSRF_HEADER_NAME)
        cookie_token = request.cookies.get(CSRF_COOKIE_NAME)
        if (
            not header_token
            or header_token != cookie_token
            or header_token != session.csrf_token
        ):
            raise ForbiddenError("missing or invalid CSRF token")

    return session


def require_admin(session: SessionRecord = Depends(require_session)) -> SessionRecord:
    if session.role != "admin":
        raise ForbiddenError("admin role required")
    return session
