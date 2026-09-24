"""Typed API errors and their FastAPI exception handlers (Task 13.1,
Requirement 18.4: "an actionable user-safe error", never a stack trace).
"""

from __future__ import annotations

import logging

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from xc_platform.api.schemas import ErrorDetail, ErrorEnvelope
from xc_platform.security.redaction import redact_string

logger = logging.getLogger("xc_platform.api")


class ApiError(Exception):
    """Base class for every error this API raises deliberately. Carries a
    stable ``code`` and an HTTP status; the message is always safe to show
    a user (never an internal exception's raw text)."""

    status_code = 500
    code = "internal_error"

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


class UnauthenticatedError(ApiError):
    status_code = 401
    code = "unauthenticated"


class ForbiddenError(ApiError):
    status_code = 403
    code = "forbidden"


class NotFoundError(ApiError):
    status_code = 404
    code = "not_found"


class ConflictError(ApiError):
    """The requested operation can't apply given the resource's current
    state (Task 12's ``IngestRunNotReadyError`` surfaces as this)."""

    status_code = 409
    code = "conflict"


class AgentDisabledError(ApiError):
    """The analytics agent's global switch is off (Task 19.4)."""

    status_code = 503
    code = "agent_disabled"


class ValidationError(ApiError):
    status_code = 422
    code = "validation_error"


def _error_response(request_id: str, error: ApiError) -> JSONResponse:
    body = ErrorEnvelope(
        request_id=request_id, error=ErrorDetail(code=error.code, message=error.message)
    )
    return JSONResponse(status_code=error.status_code, content=body.model_dump())


def install_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(ApiError)
    async def handle_api_error(request: Request, exc: ApiError) -> JSONResponse:
        request_id = getattr(request.state, "request_id", "unknown")
        return _error_response(request_id, exc)

    @app.exception_handler(Exception)
    async def handle_unexpected_error(request: Request, exc: Exception) -> JSONResponse:
        request_id = getattr(request.state, "request_id", "unknown")
        # Requirement 18.4: an actionable user-safe error, plus a redacted
        # diagnostic event for the administrator -- never the raw exception
        # text in the response body.
        logger.error(
            "request_id=%s unhandled error: %s", request_id, redact_string(str(exc))
        )
        return _error_response(request_id, ApiError("an unexpected error occurred"))
