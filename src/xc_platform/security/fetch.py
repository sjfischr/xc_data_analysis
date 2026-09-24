"""Policy-guarded HTTP GET/POST: validate, fetch, revalidate every redirect.

``urllib.request``'s default redirect handling follows a ``Location`` header
without re-checking it against any policy, which is exactly the SSRF gap
Requirement 4.8 calls out ("revalidate every redirect"). This module installs
a redirect handler that refuses to auto-follow, so the caller's loop can
validate each hop's destination with
:func:`xc_platform.security.url_policy.validate_destination` before issuing
the next request.
"""

from __future__ import annotations

import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any
from urllib.parse import urljoin

from xc_platform.security.url_policy import URLPolicyError, validate_destination

DEFAULT_MAX_BODY_BYTES = 8 << 20  # 8 MiB
DEFAULT_MAX_REDIRECTS = 5
DEFAULT_TIMEOUT_S = 20.0
_REDIRECT_STATUSES = frozenset({301, 302, 303, 307, 308})


class FetchRefusedError(RuntimeError):
    """A request or one of its redirects failed URL/network policy."""


class TooManyRedirectsError(RuntimeError):
    """A request followed more redirect hops than ``max_redirects`` allows."""


class FetchNetworkError(RuntimeError):
    """A network-level failure occurred (DNS, connection, or timeout)."""


@dataclass(frozen=True, slots=True)
class FetchResponse:
    status: int
    body: bytes
    final_url: str
    elapsed_s: float
    redirect_count: int


class _NoFollowRedirectHandler(urllib.request.HTTPRedirectHandler):
    """Never auto-follow -- return the 3xx response as-is for the caller."""

    def redirect_request(
        self, req: Any, fp: Any, code: int, msg: str, headers: Any, newurl: str
    ) -> Any:
        return None


_OPENER = urllib.request.build_opener(_NoFollowRedirectHandler)


def fetch(
    url: str,
    *,
    allowed_hosts: frozenset[str],
    method: str = "GET",
    body: bytes | None = None,
    headers: dict[str, str] | None = None,
    timeout_s: float = DEFAULT_TIMEOUT_S,
    max_body_bytes: int = DEFAULT_MAX_BODY_BYTES,
    max_redirects: int = DEFAULT_MAX_REDIRECTS,
) -> FetchResponse:
    """GET or POST ``url``, following redirects only after re-validating each.

    Raises :class:`FetchRefusedError` if the initial URL or any redirect
    target fails :func:`~xc_platform.security.url_policy.validate_destination`,
    and :class:`TooManyRedirectsError` if more than ``max_redirects`` hops
    occur. Response bodies are capped at ``max_body_bytes``.
    """
    started = time.monotonic()
    current_url = url
    redirect_count = 0
    request_headers = dict(headers or {})
    request_headers.setdefault("User-Agent", "xc-data-platform/1.0 (+intake adapter)")

    while True:
        try:
            validate_destination(current_url, allowed_hosts=allowed_hosts)
        except URLPolicyError as exc:
            raise FetchRefusedError(str(exc)) from exc

        req = urllib.request.Request(  # noqa: S310 -- policy enforced above
            current_url, data=body, headers=request_headers, method=method
        )
        try:
            with _OPENER.open(req, timeout=timeout_s) as response:
                payload = response.read(max_body_bytes)
                status = int(response.status)
                final_url = response.geturl()
                response_headers = response.headers
        except urllib.error.HTTPError as error:
            if error.code in _REDIRECT_STATUSES:
                status = error.code
                payload = b""
                final_url = current_url
                response_headers = error.headers
            else:
                payload = error.read(max_body_bytes)
                return FetchResponse(
                    status=error.code,
                    body=payload,
                    final_url=current_url,
                    elapsed_s=round(time.monotonic() - started, 3),
                    redirect_count=redirect_count,
                )
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise FetchNetworkError(
                f"network error fetching {current_url!r}: {exc}"
            ) from exc

        if status in _REDIRECT_STATUSES:
            location = response_headers.get("Location") if response_headers else None
            if not location:
                raise FetchRefusedError(
                    f"redirect status {status} from {current_url!r} carried no "
                    "Location header"
                )
            redirect_count += 1
            if redirect_count > max_redirects:
                raise TooManyRedirectsError(
                    f"exceeded {max_redirects} redirects starting from {url!r}"
                )
            current_url = urljoin(current_url, location)
            continue

        return FetchResponse(
            status=status,
            body=payload,
            final_url=final_url,
            elapsed_s=round(time.monotonic() - started, 3),
            redirect_count=redirect_count,
        )
