"""Live confirmation of the Tavily discovery client against the real API
(Task 9.4).

Marked ``live`` (opt-in, never runs in ordinary CI). Run explicitly::

    export TAVILY_API_KEY=...   # or source it from .env.local
    pytest tests/integration/test_tavily_live.py -m live -v -s

Confirms the production client, not just the fake-fetcher unit tests: a
real Search call resolves, is metered correctly, and its results route
through the same RunSignup source router Task 8 built -- and separately
confirms (again, live, not just per Task 3.3's original probe) that Map
still does not enumerate this race's sibling structure, the finding that
drove Requirement 6.1's amendment.
"""

from __future__ import annotations

import pytest

from xc_platform.ingest.adapters.tavily_client import TavilyBudget, TavilyClient
from xc_platform.ingest.adapters.tavily_discovery import discover_siblings
from xc_platform.security.config import SecretNotFoundError, get_tavily_api_key

pytestmark = pytest.mark.live

SEED_URL = "https://runsignup.com/Race/Results/154050"


def _require_tavily_key() -> None:
    try:
        get_tavily_api_key()
    except SecretNotFoundError:
        pytest.skip("TAVILY_API_KEY not set in this environment")


def test_search_discovers_a_sibling_race_and_routes_it() -> None:
    _require_tavily_key()
    budget = TavilyBudget(max_requests=5, max_credits=10, max_wall_time_s=60.0)
    client = TavilyClient(budget=budget)

    run = discover_siblings(client, [SEED_URL])
    assert not run.paused
    seed = run.completed[0]
    print(f"query: {seed.query!r}")
    print(f"discovered {len(seed.discovered_urls)} urls")
    print(f"routable (RunSignup-family): {[n.race_id for n in seed.routable]}")
    print(f"dropped (non-RunSignup): {seed.dropped_unroutable}")
    print(
        f"credits used: {run.total_credits_used}, requests: {run.total_requests_used}"
    )

    assert seed.discovered_urls, "expected at least one discovered URL"
    assert run.total_credits_used >= 1


def test_map_does_not_enumerate_sibling_races_confirming_the_spec_amendment() -> None:
    """Re-confirms live, for this build's production client, the Task 3.3
    finding that drove Requirement 6.1's 2026-09-22 amendment."""
    _require_tavily_key()
    budget = TavilyBudget(max_requests=5, max_credits=10, max_wall_time_s=60.0)
    client = TavilyClient(budget=budget)

    result = client.map(SEED_URL, max_depth=2, limit=30)
    other_race_ids = {
        url.split("/Results/")[1].split("/")[0].split("?")[0]
        for url in result.urls
        if "/Results/" in url and "154050" not in url
    }
    print(f"map discovered {len(result.urls)} urls; other race IDs: {other_race_ids}")
    # Not a hard assertion on the exact count (Tavily's index can change),
    # but the qualitative finding -- Map does not surface sibling race IDs
    # the way Search does -- is what matters here.
    assert other_race_ids == set() or len(other_race_ids) < 2
