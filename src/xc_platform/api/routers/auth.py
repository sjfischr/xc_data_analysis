"""Login/logout endpoints (Task 13.4, design.md section 6.2's cookie
session / BFF pattern).

``GET /auth/login`` and ``GET /auth/callback`` (Task 18.2) are the real
Cognito hosted-UI authorization-code flow: ``login`` redirects the browser
to the user pool's hosted UI; ``callback`` exchanges the returned code for
tokens at Cognito's token endpoint, verifies the ID token's signature
against the pool's JWKS (never trusts an unverified token), and reads the
``cognito:groups`` claim for role. :func:`login_with_verified_identity` is
the seam both this and ``dev-login`` call once a subject/role is already
verified -- exercised directly by most of the tests below so the
session/cookie machinery is covered without a live IdP; the OAuth routes
themselves are tested with :func:`verify_cognito_id_token` and
:func:`_exchange_code_for_tokens` monkeypatched, the same pattern
``security/config.py``'s SSM backend tests use.

``POST /auth/dev-login`` is a **local-only stand-in** for that callback, so
the session/cookie machinery downstream (:mod:`xc_platform.api.deps`,
:mod:`xc_platform.security.session`) is exercised end to end by ordinary
tests without a live IdP. It lives on its own ``dev_router`` (rather than
``router``) precisely so ``api/app.py``'s ``create_app`` can leave it out
by construction -- not by a docstring promise -- whenever it is not
explicitly opted into (``run_production_api.py`` never opts in). A
production build replaces it with the real Cognito authorization-code
callback; it would call the exact same
:func:`login_with_verified_identity` function once it has verified the
token.
"""

from __future__ import annotations

from typing import Any
from urllib.parse import urlencode

import httpx
import jwt
from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.responses import RedirectResponse
from jwt import PyJWKClient

from xc_platform.agents.identity import derive_actor_id
from xc_platform.api.context import AppContext, CognitoOAuthConfig
from xc_platform.api.deps import get_context, require_session
from xc_platform.api.errors import ConflictError
from xc_platform.security.session import (
    CSRF_COOKIE_NAME,
    SESSION_COOKIE_NAME,
    SessionRecord,
)

router = APIRouter(prefix="/api/v1/auth", tags=["auth"])
dev_router = APIRouter(prefix="/api/v1/auth", tags=["auth"])


def _require_oauth_config(ctx: AppContext) -> CognitoOAuthConfig:
    if ctx.cognito_oauth is None:
        raise HTTPException(status_code=503, detail="Cognito OAuth is not configured")
    return ctx.cognito_oauth


def _redirect_uri(cfg: CognitoOAuthConfig, request_base_url: str) -> str:
    # The registered callback in Cognito's app client is the App Runner
    # service's own URL (web_stack.py's AppClient) -- deriving the host from
    # the incoming request rather than a separately-injected config value
    # means this needs no XC_API_BASE_URL env var and keeps working if the
    # service is ever reached through more than one hostname. The *scheme*
    # is always forced to https regardless of what the request reports:
    # App Runner terminates TLS at its own front door and forwards to this
    # container over plain HTTP, so `request.base_url` reports "http" even
    # though the real, public-facing, Cognito-registered URL is "https" --
    # trusting it verbatim produces a redirect_uri Cognito rejects as a
    # mismatch against the registered callback (found live during Task
    # 18.2's validation, 2026-09-23). There is no real deployment of this
    # app that is ever legitimately reachable over plain http.
    host = request_base_url.split("://", 1)[-1].rstrip("/")
    return f"https://{host}/api/v1/auth/callback"


def _exchange_code_for_tokens(
    code: str, *, cfg: CognitoOAuthConfig, redirect_uri: str
) -> dict[str, Any]:
    response = httpx.post(
        f"https://{cfg.domain}/oauth2/token",
        data={
            "grant_type": "authorization_code",
            "client_id": cfg.client_id,
            "code": code,
            "redirect_uri": redirect_uri,
        },
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        timeout=10.0,
    )
    response.raise_for_status()
    payload: dict[str, Any] = response.json()
    return payload


def verify_cognito_id_token(
    id_token: str, *, cfg: CognitoOAuthConfig
) -> dict[str, Any]:
    """Verify signature (against the pool's live JWKS), issuer, audience,
    and expiry; then require ``token_use == "id"`` so an access token can
    never be mistaken for a verified identity. Raises on any failure -- a
    caller must never treat an unverified token as trusted."""
    jwks_url = (
        f"https://cognito-idp.{cfg.region}.amazonaws.com/"
        f"{cfg.user_pool_id}/.well-known/jwks.json"
    )
    signing_key = PyJWKClient(jwks_url).get_signing_key_from_jwt(id_token)
    issuer = f"https://cognito-idp.{cfg.region}.amazonaws.com/{cfg.user_pool_id}"
    claims: dict[str, Any] = jwt.decode(
        id_token,
        signing_key.key,
        algorithms=["RS256"],
        audience=cfg.client_id,
        issuer=issuer,
    )
    if claims.get("token_use") != "id":
        raise jwt.InvalidTokenError("expected an ID token")
    return claims


def _set_session_cookies(response: Response, record: SessionRecord) -> None:
    # Secure/HttpOnly/SameSite per Requirement 13.6 -- the session cookie
    # is never readable from JavaScript. The CSRF cookie is deliberately
    # NOT HttpOnly: the frontend reads it and echoes it back in a header
    # on mutating requests (the standard double-submit pattern), which
    # only works if JavaScript can read it.
    #
    # `samesite="none"` (not "lax"): found live during Task 13/14's first
    # real deployment, 2026-09-23 -- the frontend (CloudFront) and this
    # API (App Runner) are genuinely different sites, not just different
    # origins on one site (design.md's original single-CloudFront-origin
    # architecture was already a documented deviation, web_stack.py's own
    # docstring). "Lax" cookies are sent on a top-level navigation (so
    # login itself worked) but never on the `fetch()` calls the SPA makes
    # afterward -- every subsequent `/auth/session` call silently 401'd.
    # "None" requires `secure=True` (already set) and is safe specifically
    # because the double-submit CSRF pattern above already exists for
    # exactly this cross-site scenario -- it is not an additional exposure
    # this change introduces, it is the defense this change now actually
    # needs to rely on.
    response.set_cookie(
        SESSION_COOKIE_NAME,
        record.session_id,
        httponly=True,
        secure=True,
        samesite="none",
        max_age=int(record.expires_at - record.created_at),
    )
    response.set_cookie(
        CSRF_COOKIE_NAME,
        record.csrf_token,
        httponly=False,
        secure=True,
        samesite="none",
        max_age=int(record.expires_at - record.created_at),
    )


AGENT_USERS_GROUP = "agent-users"


def login_with_verified_identity(
    ctx: AppContext,
    response: Response,
    *,
    subject: str,
    role: str,
    agent_access: bool = False,
) -> SessionRecord:
    """Create a session for an identity a caller has already verified.

    ``subject`` is the Cognito ``sub`` claim -- never stored directly (only
    its HMAC, design.md 12.4) and never logged.
    """
    actor_id = derive_actor_id(subject, ctx.actor_id_application_key)
    record = ctx.session_store.create(
        actor_id=actor_id, role=role, agent_access=agent_access
    )
    _set_session_cookies(response, record)
    return record


@dev_router.post("/dev-login", include_in_schema=False)
def dev_login(
    response: Response,
    subject: str,
    role: str = "viewer",
    agent_access: bool = False,
    ctx: AppContext = Depends(get_context),
) -> dict[str, str]:
    record = login_with_verified_identity(
        ctx, response, subject=subject, role=role, agent_access=agent_access
    )
    return {"actor_id": record.actor_id, "role": record.role}


@router.get("/login")
def login(request: Request, ctx: AppContext = Depends(get_context)) -> RedirectResponse:
    cfg = _require_oauth_config(ctx)
    redirect_uri = _redirect_uri(cfg, str(request.base_url))
    params = {
        "client_id": cfg.client_id,
        "response_type": "code",
        "scope": "openid email profile",
        "redirect_uri": redirect_uri,
    }
    return RedirectResponse(
        f"https://{cfg.domain}/oauth2/authorize?{urlencode(params)}"
    )


@router.get("/callback", response_model=None)
def callback(
    code: str,
    request: Request,
    response: Response,
    ctx: AppContext = Depends(get_context),
) -> dict[str, str] | RedirectResponse:
    cfg = _require_oauth_config(ctx)
    redirect_uri = _redirect_uri(cfg, str(request.base_url))

    try:
        tokens = _exchange_code_for_tokens(code, cfg=cfg, redirect_uri=redirect_uri)
    except httpx.HTTPStatusError as error:
        # Cognito authorization codes are single-use (found live during
        # Task 18.2's validation, 2026-09-23: a duplicate GET to this route
        # -- a browser refresh or back-button replaying the same callback
        # URL -- resubmitted an already-exchanged code and Cognito's token
        # endpoint correctly 400'd it; that surfaced as a raw, unhandled
        # 500 before this). Never expose Cognito's own response body.
        raise ConflictError(
            "This login link was already used or has expired. Please log in again."
        ) from error
    claims = verify_cognito_id_token(tokens["id_token"], cfg=cfg)

    groups = claims.get("cognito:groups", [])
    role = "admin" if "admin" in groups else "viewer"
    agent_access = AGENT_USERS_GROUP in groups

    if ctx.frontend_origin:
        # A real browser completing the hosted-UI flow lands here via a
        # full top-level navigation -- it must land on the frontend's own
        # page, not a raw JSON body. Build the redirect first and set
        # cookies on it directly: returning a Response object from a path
        # operation replaces the injected `response` dependency entirely,
        # so cookies set on the latter would otherwise be silently lost.
        redirect = RedirectResponse(f"{ctx.frontend_origin}/dashboard")
        login_with_verified_identity(
            ctx,
            redirect,
            subject=claims["sub"],
            role=role,
            agent_access=agent_access,
        )
        return redirect

    record = login_with_verified_identity(
        ctx, response, subject=claims["sub"], role=role, agent_access=agent_access
    )
    return {"actor_id": record.actor_id, "role": record.role}


@router.post("/logout")
def logout(
    response: Response,
    ctx: AppContext = Depends(get_context),
    session: SessionRecord = Depends(require_session),
) -> dict[str, str]:
    ctx.session_store.revoke(session.session_id)
    response.delete_cookie(SESSION_COOKIE_NAME)
    response.delete_cookie(CSRF_COOKIE_NAME)
    return {"status": "logged_out"}


@router.get("/session")
def session_info(
    session: SessionRecord = Depends(require_session),
) -> dict[str, str | bool]:
    return {
        "actor_id": session.actor_id,
        "role": session.role,
        "agent_access": session.agent_access,
    }
