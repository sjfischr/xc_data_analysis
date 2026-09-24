"""Fetch mechanics: redirect revalidation, size cap, error passthrough.

Runs a real local HTTP server so the redirect-following and body-reading
code paths are genuinely exercised, not mocked. The SSRF policy itself
(:func:`xc_platform.security.url_policy.validate_destination`) has its own
full test suite in ``test_url_policy.py``; here it is replaced with a
recording stub so these tests can use a loopback server (which the real
policy correctly refuses) while still proving that every redirect hop is
independently re-validated -- the exact property Requirement 4.8 requires.
"""

from __future__ import annotations

import threading
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import cast

import pytest

import xc_platform.security.fetch as fetch_module
from xc_platform.security.fetch import (
    FetchNetworkError,
    FetchRefusedError,
    TooManyRedirectsError,
    fetch,
)
from xc_platform.security.url_policy import URLPolicyError


class _Handler(BaseHTTPRequestHandler):
    def log_message(self, *args: object) -> None:  # silence test output
        pass

    @property
    def _port(self) -> int:
        return cast(HTTPServer, self.server).server_port

    def do_GET(self) -> None:  # noqa: N802 -- BaseHTTPRequestHandler's contract
        if self.path == "/ok":
            body = b'{"ok": true}'
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        elif self.path == "/redirect-once":
            self.send_response(302)
            self.send_header("Location", f"http://127.0.0.1:{self._port}/ok")
            self.end_headers()
        elif self.path.startswith("/redirect-chain/"):
            n = int(self.path.rsplit("/", 1)[-1])
            if n <= 0:
                self.send_response(200)
                body = b'{"chain_done": true}'
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            else:
                self.send_response(302)
                next_path = f"/redirect-chain/{n - 1}"
                self.send_header(
                    "Location",
                    f"http://127.0.0.1:{self._port}{next_path}",
                )
                self.end_headers()
        elif self.path == "/big":
            self.send_response(200)
            body = b"x" * 1000
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        elif self.path == "/not-found":
            self.send_response(404)
            body = b'{"error": "not found"}'
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        elif self.path == "/redirect-no-location":
            self.send_response(302)
            self.end_headers()
        else:
            self.send_response(404)
            self.end_headers()


@pytest.fixture(scope="module")
def server() -> Iterator[HTTPServer]:
    httpd = HTTPServer(("127.0.0.1", 0), _Handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        yield httpd
    finally:
        httpd.shutdown()
        thread.join(timeout=5)


@pytest.fixture
def validated_urls(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Stub out SSRF policy with a recording pass-through (see module docstring)."""
    seen: list[str] = []

    def fake_validate(url: str, *, allowed_hosts: frozenset[str]) -> None:
        seen.append(url)

    monkeypatch.setattr(fetch_module, "validate_destination", fake_validate)
    return seen


def _base_url(server: HTTPServer) -> str:
    return f"http://127.0.0.1:{server.server_port}"


def test_successful_get(server: HTTPServer, validated_urls: list[str]) -> None:
    response = fetch(f"{_base_url(server)}/ok", allowed_hosts=frozenset({"127.0.0.1"}))
    assert response.status == 200
    assert response.body == b'{"ok": true}'
    assert response.redirect_count == 0
    assert validated_urls == [f"{_base_url(server)}/ok"]


def test_single_redirect_is_followed_and_revalidated(
    server: HTTPServer, validated_urls: list[str]
) -> None:
    response = fetch(
        f"{_base_url(server)}/redirect-once", allowed_hosts=frozenset({"127.0.0.1"})
    )
    assert response.status == 200
    assert response.body == b'{"ok": true}'
    assert response.redirect_count == 1
    # Both the original URL and the redirect target were independently
    # validated -- this is the property Requirement 4.8 requires.
    assert validated_urls == [
        f"{_base_url(server)}/redirect-once",
        f"{_base_url(server)}/ok",
    ]


def test_redirect_chain_within_the_limit_succeeds(
    server: HTTPServer, validated_urls: list[str]
) -> None:
    response = fetch(
        f"{_base_url(server)}/redirect-chain/3",
        allowed_hosts=frozenset({"127.0.0.1"}),
        max_redirects=5,
    )
    assert response.status == 200
    assert response.redirect_count == 3
    assert len(validated_urls) == 4  # 1 initial + 3 redirect hops


def test_exceeding_max_redirects_raises(
    server: HTTPServer, validated_urls: list[str]
) -> None:
    with pytest.raises(TooManyRedirectsError):
        fetch(
            f"{_base_url(server)}/redirect-chain/10",
            allowed_hosts=frozenset({"127.0.0.1"}),
            max_redirects=3,
        )


def test_redirect_without_location_header_is_refused(
    server: HTTPServer, validated_urls: list[str]
) -> None:
    with pytest.raises(FetchRefusedError, match="Location"):
        fetch(
            f"{_base_url(server)}/redirect-no-location",
            allowed_hosts=frozenset({"127.0.0.1"}),
        )


def test_non_redirect_http_error_is_returned_not_raised(
    server: HTTPServer, validated_urls: list[str]
) -> None:
    response = fetch(
        f"{_base_url(server)}/not-found", allowed_hosts=frozenset({"127.0.0.1"})
    )
    assert response.status == 404
    assert b"not found" in response.body


def test_response_body_is_capped(server: HTTPServer, validated_urls: list[str]) -> None:
    response = fetch(
        f"{_base_url(server)}/big",
        allowed_hosts=frozenset({"127.0.0.1"}),
        max_body_bytes=100,
    )
    assert len(response.body) == 100


def test_url_policy_rejection_is_wrapped_as_fetch_refused(
    server: HTTPServer, monkeypatch: pytest.MonkeyPatch
) -> None:
    def always_refuse(url: str, *, allowed_hosts: frozenset[str]) -> None:
        raise URLPolicyError("refused for test")

    monkeypatch.setattr(fetch_module, "validate_destination", always_refuse)
    with pytest.raises(FetchRefusedError, match="refused for test"):
        fetch(f"{_base_url(server)}/ok", allowed_hosts=frozenset({"127.0.0.1"}))


def test_connection_failure_raises_fetch_network_error(
    validated_urls: list[str],
) -> None:
    # Port 1 is reserved/unlikely to be listening; the connection must fail.
    with pytest.raises(FetchNetworkError):
        fetch(
            "http://127.0.0.1:1/unreachable",
            allowed_hosts=frozenset({"127.0.0.1"}),
            timeout_s=2.0,
        )
