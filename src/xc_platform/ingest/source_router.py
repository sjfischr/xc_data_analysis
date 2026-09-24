"""Source router (design.md section 9.2), release-1 scope.

Release 1 ingests RunSignup-family sources only (owner decision,
2026-09-21: non-RunSignup extraction fallback dropped -- Requirement 6).
Tavily's role narrows to discovering sibling RunSignup pages (Task 9), never
a competing result-producing adapter, so this router has exactly one
adapter to select -- matching design.md section 9.2 step 5 ("Select
RunSignupAdapter...") and step 7 ("Reject unknown, disallowed, or
non-RunSignup-family sources") literally for what release 1 actually needs.

:func:`normalize_intake_url` covers steps 1-4 of the router algorithm
(parse/canonicalize, require HTTPS, recognize scope, reject non-family
hosts); the SSRF network/redirect checks (step 2's "resolve DNS and reject
loopback/private/metadata destinations", step 3's redirect revalidation)
happen later, at actual fetch time, in
:mod:`xc_platform.security.url_policy`/:mod:`xc_platform.security.fetch` --
they depend on live DNS resolution, which a pure URL-parsing step must not
require.
"""

from __future__ import annotations

from xc_platform.ingest.adapters.runsignup import (
    RunSignupAdapter,
    UnsupportedRunSignupUrlError,
    normalize_runsignup_url,
)
from xc_platform.ingest.adapters.runsignup_client import RunSignupClient
from xc_platform.ingest.contract import NormalizedUrl


class UnsupportedSourceError(ValueError):
    """No adapter in release 1 can handle this URL (design.md section 9.2 step 7)."""


def normalize_intake_url(url: str) -> NormalizedUrl:
    """Parse and canonicalize an administrator-submitted intake URL.

    Raises :class:`UnsupportedSourceError` for anything that is not a
    RunSignup-family results URL -- release 1 has no other adapter to route
    it to.
    """
    try:
        return normalize_runsignup_url(url)
    except UnsupportedRunSignupUrlError as exc:
        raise UnsupportedSourceError(str(exc)) from exc


def select_adapter(
    normalized_url: NormalizedUrl, client: RunSignupClient
) -> RunSignupAdapter:
    """Select the adapter for an already-normalized URL (router step 5)."""
    adapter = RunSignupAdapter(client=client)
    if not adapter.can_handle(normalized_url):
        raise UnsupportedSourceError(
            "no adapter in release 1 handles source_namespace="
            f"{normalized_url.source_namespace!r}"
        )
    return adapter
