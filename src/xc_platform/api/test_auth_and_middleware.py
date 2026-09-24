from __future__ import annotations

import dataclasses
import time

import httpx
import pytest
from fastapi.testclient import TestClient

from xc_platform.api.app import create_app
from xc_platform.api.conftest import login
from xc_platform.api.context import AppContext, CognitoOAuthConfig
from xc_platform.api.routers import auth as auth_router


def test_health_is_public(client: TestClient) -> None:
    response = client.get("/api/v1/system/health")
    assert response.status_code == 200
    assert response.json() == {"ok": True}


def test_every_response_carries_a_request_id_and_security_headers(
    client: TestClient,
) -> None:
    response = client.get("/api/v1/system/health")
    assert response.headers["X-Request-Id"]
    assert response.headers["X-Content-Type-Options"] == "nosniff"
    assert response.headers["X-Frame-Options"] == "DENY"
    assert "Content-Security-Policy" in response.headers


def test_a_protected_route_without_a_session_is_401(client: TestClient) -> None:
    response = client.get("/api/v1/catalog/filters")
    assert response.status_code == 401
    body = response.json()
    assert body["error"]["code"] == "unauthenticated"


def test_login_sets_session_and_csrf_cookies(client: TestClient) -> None:
    response = client.post(
        "/api/v1/auth/dev-login", params={"subject": "user-1", "role": "viewer"}
    )
    assert response.status_code == 200
    assert "xc_session" in response.cookies
    assert "xc_csrf" in response.cookies


def test_session_cookies_are_samesite_none_for_the_real_cross_site_deployment(
    client: TestClient,
) -> None:
    """Regression test for the 401-after-login bug found live during
    Task 13/14's first real deployment (2026-09-23): the frontend
    (CloudFront) and this API (App Runner) are different sites, so a
    "Lax" cookie is never sent on the `fetch()` calls the SPA makes after
    the initial (same-site-navigation) login -- only "None" (paired with
    the existing double-submit CSRF defense) works here."""
    response = client.post(
        "/api/v1/auth/dev-login", params={"subject": "user-1", "role": "viewer"}
    )
    set_cookie_headers = response.headers.get_list("set-cookie")
    assert len(set_cookie_headers) == 2
    for header in set_cookie_headers:
        assert "samesite=none" in header.lower()
        assert "secure" in header.lower()


def test_production_app_never_exposes_the_dev_login_bypass(ctx: AppContext) -> None:
    """Regression test for the unauthenticated admin-session-minting hole
    found live on App Runner during Task 18.1 (2026-09-23): `dev-login`
    must be unreachable, not merely undocumented, whenever a caller does
    not explicitly opt in -- exactly what `run_production_api.py` does."""
    app = create_app(ctx, include_dev_login=False)
    client = TestClient(app, base_url="https://testserver")

    response = client.post(
        "/api/v1/auth/dev-login", params={"subject": "attacker", "role": "admin"}
    )

    assert response.status_code == 404


def test_login_then_protected_route_succeeds(client: TestClient) -> None:
    login(client)
    response = client.get("/api/v1/catalog/filters")
    assert response.status_code == 200
    assert response.json()["publication_id"]


def test_actor_id_is_pseudonymous_not_the_raw_subject(client: TestClient) -> None:
    login(client, subject="coach.jane@example.com")
    response = client.get("/api/v1/auth/session")
    assert response.status_code == 200
    assert "coach.jane" not in response.json()["actor_id"]


def test_logout_then_protected_route_is_401_again(client: TestClient) -> None:
    login(client)
    assert client.get("/api/v1/catalog/filters").status_code == 200

    logout_response = client.post("/api/v1/auth/logout")
    assert logout_response.status_code == 200

    assert client.get("/api/v1/catalog/filters").status_code == 401


def test_a_mutating_request_without_a_csrf_header_is_rejected(
    client: TestClient,
) -> None:
    login(client, role="admin")
    del client.headers[
        "X-CSRF-Token"
    ]  # simulate a cross-site request that can't read the cookie
    response = client.post("/api/v1/auth/logout")
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "forbidden"


def test_a_mutating_request_with_a_wrong_csrf_header_is_rejected(
    client: TestClient,
) -> None:
    login(client, role="admin")
    client.headers["X-CSRF-Token"] = "not-the-real-token"
    response = client.post("/api/v1/auth/logout")
    assert response.status_code == 403


def test_a_get_request_needs_no_csrf_header(client: TestClient) -> None:
    login(client, role="admin")
    del client.headers["X-CSRF-Token"]
    response = client.get("/api/v1/catalog/filters")
    assert response.status_code == 200


def test_viewer_cannot_reach_an_admin_only_route(client: TestClient) -> None:
    login(client, role="viewer")
    response = client.post(
        "/api/v1/ingest-runs", json={"url": "https://runsignup.com/Race/Results/1"}
    )
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "forbidden"


def test_an_expired_session_is_401_at_the_http_layer(
    client: TestClient, ctx: AppContext
) -> None:
    """Task 16.1: the same expiration property security/test_session.py
    proves at the unit level, exercised here through a real HTTP request."""
    record = ctx.session_store.create(actor_id="actor-1", role="viewer", ttl_seconds=0)
    time.sleep(0.01)
    client.cookies.set("xc_session", record.session_id)
    response = client.get("/api/v1/catalog/filters")
    assert response.status_code == 401


def test_no_api_response_ever_includes_raw_source_fields(
    client: TestClient, scripted_client_factory: object
) -> None:
    """Requirement 13.5: unnecessary registration fields never reach a
    viewer response. staged_results.raw_fields_json (the full unfiltered
    source row) is never serialized into any response body this API
    returns -- every admin_ingest.py route returns only the typed
    Pydantic response models in api/schemas.py, none of which include a
    raw-fields field."""
    login(client, role="admin")
    response = client.get("/api/v1/catalog/filters")
    assert "raw_fields" not in response.text
    assert "date_of_birth" not in response.text.lower()
    assert "phone" not in response.text.lower()


def test_a_404_is_a_typed_error_not_a_stack_trace(client: TestClient) -> None:
    login(client, role="admin")
    response = client.get("/api/v1/ingest-runs/does-not-exist")
    assert response.status_code == 404
    body = response.json()
    assert body["error"]["code"] == "not_found"
    assert "Traceback" not in response.text


_OAUTH_CFG = CognitoOAuthConfig(
    domain="xc-platform-test.auth.us-east-1.amazoncognito.com",
    client_id="test-client-id",
    user_pool_id="us-east-1_TestPool",
    region="us-east-1",
)


def test_oauth_login_503s_when_not_configured(client: TestClient) -> None:
    response = client.get("/api/v1/auth/login", follow_redirects=False)
    assert response.status_code == 503


def test_oauth_callback_503s_when_not_configured(client: TestClient) -> None:
    response = client.get("/api/v1/auth/callback", params={"code": "whatever"})
    assert response.status_code == 503


def test_oauth_redirect_uri_is_always_https_even_behind_a_plain_http_proxy(
    ctx: AppContext,
) -> None:
    """Regression test for the redirect mismatch found live during Task
    18.2 (2026-09-23): App Runner terminates TLS at its own front door and
    forwards to the container over plain HTTP, so the incoming request's
    scheme is "http" even though the real, Cognito-registered URL is
    "https". The generated redirect_uri must not trust that scheme."""
    configured_ctx = dataclasses.replace(ctx, cognito_oauth=_OAUTH_CFG)
    configured_client = TestClient(
        create_app(configured_ctx), base_url="http://testserver"
    )

    response = configured_client.get("/api/v1/auth/login", follow_redirects=False)

    location = response.headers["location"]
    assert (
        "redirect_uri=https%3A%2F%2Ftestserver%2Fapi%2Fv1%2Fauth%2Fcallback" in location
    )


def test_oauth_login_redirects_to_the_hosted_ui_authorize_endpoint(
    ctx: AppContext,
) -> None:
    configured_ctx = dataclasses.replace(ctx, cognito_oauth=_OAUTH_CFG)
    configured_client = TestClient(
        create_app(configured_ctx), base_url="https://testserver"
    )

    response = configured_client.get("/api/v1/auth/login", follow_redirects=False)

    assert response.status_code in (302, 307)
    location = response.headers["location"]
    assert location.startswith(f"https://{_OAUTH_CFG.domain}/oauth2/authorize?")
    assert "client_id=test-client-id" in location
    assert "response_type=code" in location
    assert (
        "redirect_uri=https%3A%2F%2Ftestserver%2Fapi%2Fv1%2Fauth%2Fcallback" in location
    )


def test_oauth_callback_verifies_the_token_and_creates_an_admin_session(
    ctx: AppContext, monkeypatch: pytest.MonkeyPatch
) -> None:
    configured_ctx = dataclasses.replace(ctx, cognito_oauth=_OAUTH_CFG)
    configured_client = TestClient(
        create_app(configured_ctx), base_url="https://testserver"
    )

    monkeypatch.setattr(
        auth_router,
        "_exchange_code_for_tokens",
        lambda code, *, cfg, redirect_uri: {"id_token": "fake-id-token"},
    )
    monkeypatch.setattr(
        auth_router,
        "verify_cognito_id_token",
        lambda id_token, *, cfg: {
            "sub": "cognito-subject-1",
            "cognito:groups": ["admin"],
        },
    )

    response = configured_client.get(
        "/api/v1/auth/callback", params={"code": "auth-code-from-cognito"}
    )

    assert response.status_code == 200
    assert response.json()["role"] == "admin"
    assert "xc_session" in response.cookies


def test_oauth_callback_defaults_to_viewer_without_the_admin_group(
    ctx: AppContext, monkeypatch: pytest.MonkeyPatch
) -> None:
    configured_ctx = dataclasses.replace(ctx, cognito_oauth=_OAUTH_CFG)
    configured_client = TestClient(
        create_app(configured_ctx), base_url="https://testserver"
    )

    monkeypatch.setattr(
        auth_router,
        "_exchange_code_for_tokens",
        lambda code, *, cfg, redirect_uri: {"id_token": "fake-id-token"},
    )
    monkeypatch.setattr(
        auth_router,
        "verify_cognito_id_token",
        lambda id_token, *, cfg: {"sub": "cognito-subject-2", "cognito:groups": []},
    )

    response = configured_client.get(
        "/api/v1/auth/callback", params={"code": "auth-code-from-cognito"}
    )

    assert response.status_code == 200
    assert response.json()["role"] == "viewer"


def test_oauth_callback_redirects_to_the_frontend_when_configured(
    ctx: AppContext, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Regression test: a real browser completing the hosted-UI flow does
    a full top-level navigation back to this route -- it must land on the
    frontend's own page, not a raw JSON body (found while wiring the
    frontend's real login flow, Task 13/14)."""
    configured_ctx = dataclasses.replace(
        ctx, cognito_oauth=_OAUTH_CFG, frontend_origin="https://app.example.com"
    )
    configured_client = TestClient(
        create_app(configured_ctx), base_url="https://testserver"
    )

    monkeypatch.setattr(
        auth_router,
        "_exchange_code_for_tokens",
        lambda code, *, cfg, redirect_uri: {"id_token": "fake-id-token"},
    )
    monkeypatch.setattr(
        auth_router,
        "verify_cognito_id_token",
        lambda id_token, *, cfg: {"sub": "cognito-subject-3", "cognito:groups": []},
    )

    response = configured_client.get(
        "/api/v1/auth/callback",
        params={"code": "auth-code-from-cognito"},
        follow_redirects=False,
    )

    assert response.status_code in (302, 307)
    assert response.headers["location"] == "https://app.example.com/dashboard"
    assert "xc_session" in response.cookies


def test_oauth_callback_with_a_reused_code_is_a_clean_409_not_a_500(
    ctx: AppContext, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Regression test for the raw 500 found live during Task 18.2's
    validation (2026-09-23): a duplicate GET to /callback (browser refresh
    or back-button replaying the URL) resubmits an already-exchanged,
    single-use Cognito authorization code. Cognito's token endpoint
    correctly 400s that; this route must turn it into a typed,
    user-actionable error, not let httpx's exception reach the generic
    500 handler."""
    configured_ctx = dataclasses.replace(ctx, cognito_oauth=_OAUTH_CFG)
    configured_client = TestClient(
        create_app(configured_ctx), base_url="https://testserver"
    )

    def _raise_invalid_grant(
        code: str, *, cfg: CognitoOAuthConfig, redirect_uri: str
    ) -> dict[str, str]:
        request = httpx.Request("POST", f"https://{cfg.domain}/oauth2/token")
        response = httpx.Response(400, request=request, json={"error": "invalid_grant"})
        raise httpx.HTTPStatusError("400", request=request, response=response)

    monkeypatch.setattr(auth_router, "_exchange_code_for_tokens", _raise_invalid_grant)

    response = configured_client.get(
        "/api/v1/auth/callback", params={"code": "already-used-code"}
    )

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "conflict"
    assert "invalid_grant" not in response.text
