"""The API app factory (Task 13.1).

Builds a fresh ``FastAPI`` instance bound to one :class:`AppContext` --
never a module-level singleton, so tests (and, eventually, the AgentCore-
style local/Lambda adapters) each get an isolated app over their own fakes.
"""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from xc_platform.api.context import AppContext
from xc_platform.api.errors import install_error_handlers
from xc_platform.api.middleware import install_middleware
from xc_platform.api.routers import admin_ingest, auth, catalog, chat, system


def create_app(
    ctx: AppContext,
    *,
    cors_allowed_origins: tuple[str, ...] = (),
    include_dev_login: bool = True,
) -> FastAPI:
    """``cors_allowed_origins`` is only needed for local development, where
    the Next.js dev server (``http://localhost:3000``) and this API run on
    different origins. In production, CloudFront serves both the static
    frontend and (via a second behavior) this API from one origin
    (design.md 15.2), so no cross-origin request -- and no CORS
    configuration -- exists there at all; passing origins here is
    explicitly opt-in, never a wildcard, and ``allow_credentials=True``
    only works with an explicit origin list regardless.

    ``include_dev_login`` defaults to True so the existing test suite (and
    ``run_local_api.py``) keep exercising the session/cookie path without a
    live Cognito pool, exactly as documented in
    ``routers/auth.py``. ``run_production_api.py`` is the one caller that
    explicitly passes False -- an unauthenticated admin-session-minting
    endpoint must never be reachable by construction on a real deployment,
    not merely by a docstring promise (found live and exploitable on the
    real App Runner deployment during Task 18.1's validation, 2026-09-23,
    and closed here rather than left as a known gap)."""
    app = FastAPI(title="XC Data Platform API", version="1")
    app.state.ctx = ctx

    if cors_allowed_origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=list(cors_allowed_origins),
            allow_credentials=True,
            allow_methods=["*"],
            allow_headers=["*"],
        )

    install_middleware(app)
    install_error_handlers(app)

    app.include_router(system.router)
    app.include_router(auth.router)
    if include_dev_login:
        app.include_router(auth.dev_router)
    app.include_router(catalog.router)
    app.include_router(admin_ingest.router)
    app.include_router(chat.router)

    return app
