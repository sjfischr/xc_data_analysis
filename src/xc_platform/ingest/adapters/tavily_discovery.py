"""Tavily-discovered sibling races, routed back to the RunSignup adapter
(Task 9.2, design.md section 9.2 step 5 / section 9.4).

Tavily discovery results are inputs to the source router, never
authoritative athlete results (design.md section 9.4): every discovered URL
is normalized through
:func:`xc_platform.ingest.source_router.normalize_intake_url`, exactly as an
administrator-submitted URL would be, and anything the router can't route
is dropped, not sent to any adapter (design.md section 9.2 step 7 -- there
is no extraction fallback in release 1). "Can't route" is not only a
non-RunSignup host: live-tested (2026-09-22), search also surfaces
RunSignup's own non-results pages (a race's registration/home page, help
articles) that correctly fail the results-URL pattern and are dropped the
same way.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from xc_platform.ingest.adapters.tavily_client import (
    TavilyBudgetExhaustedError,
    TavilyClient,
)
from xc_platform.ingest.contract import NormalizedUrl
from xc_platform.ingest.source_router import (
    UnsupportedSourceError,
    normalize_intake_url,
)
from xc_platform.security.content_safety import scan_for_injection

# Maps a seed URL to the Tavily search query string used to find its siblings.
QueryBuilder = Callable[[str], str]


@dataclass(frozen=True, slots=True)
class SeedDiscovery:
    """Everything learned from searching for siblings of one seed URL."""

    seed_url: str
    query: str
    discovered_urls: tuple[str, ...]
    routable: tuple[NormalizedUrl, ...]
    dropped_unroutable: tuple[str, ...]
    injection_signals: tuple[str, ...]
    credits_used: int
    elapsed_s: float


@dataclass(frozen=True, slots=True)
class TavilyDiscoveryRun:
    """The outcome of discovering siblings for a list of seed URLs.

    ``pending_seed_urls`` carries forward exactly the seed URLs never
    attempted because the budget ran out mid-run (Requirement 6.8: stop and
    provide a resumable status). Resuming means calling
    :func:`discover_siblings` again with ``pending_seed_urls`` and a fresh
    budget -- the seeds already in ``completed`` are never re-requested, so
    a resume never double-charges credits for finished work (Task 9.4).
    """

    completed: tuple[SeedDiscovery, ...]
    pending_seed_urls: tuple[str, ...]
    paused: bool
    total_credits_used: int
    total_requests_used: int

    @property
    def all_routable(self) -> tuple[NormalizedUrl, ...]:
        """Every routable URL across all completed seeds, deduplicated.

        Deduplicated on ``(race_id, requested_result_set_id)`` -- the same
        sibling page can legitimately surface from more than one seed's
        search (Task 9.2: "deduplicate discovered URLs").
        """
        seen: set[tuple[str, int | None]] = set()
        deduped: list[NormalizedUrl] = []
        for seed in self.completed:
            for normalized in seed.routable:
                key = (normalized.race_id, normalized.requested_result_set_id)
                if key not in seen:
                    seen.add(key)
                    deduped.append(normalized)
        return tuple(deduped)


def _default_query(seed_url: str) -> str:
    """Build a default search query when the caller has no better one.

    Deliberately does NOT embed the raw seed URL: live-tested (2026-09-22)
    against the real API, embedding the URL biased results back toward
    that exact race (the same race ID, 4 times, plus unrelated
    runsignup.com help/about pages) rather than finding siblings. A
    domain-relevant phrase without the URL found the real sibling meet
    (154708) and a race ID this adapter's static config didn't even know
    about (121442). A fully generic query ("cross country race results
    site:runsignup.com") performed far worse still -- the bare word "race"
    pulled in unrelated results about ethnicity/social race, and Tavily's
    `site:` operator did not hard-restrict the domain.

    This default is tuned to this deployment (the NVJCYO series), exactly
    like this adapter's own ``MEET_RACE_IDS`` config
    (xc_platform.ingest.adapters.runsignup) -- not a claim that it
    generalizes to an arbitrary RunSignup organization. A caller that knows
    the actual race/series name (from the RunSignup adapter's own
    ``discover()``) should pass a better query via ``query_builder``.
    """
    return "NVJCYO cross country race results runsignup.com"


def discover_siblings(
    client: TavilyClient,
    seed_urls: list[str],
    *,
    query_builder: QueryBuilder | None = None,
    max_results: int = 10,
) -> TavilyDiscoveryRun:
    """Search for sibling pages of each seed URL, routing results back to
    the RunSignup adapter.

    Stops (without raising) the instant the budget is exhausted, recording
    which seeds were never attempted so the caller can resume later
    (Requirement 6.8).
    """
    build_query = query_builder or _default_query
    completed: list[SeedDiscovery] = []

    for index, seed_url in enumerate(seed_urls):
        try:
            client.budget.check(note=f"search for siblings of {seed_url}")
        except TavilyBudgetExhaustedError:
            return TavilyDiscoveryRun(
                completed=tuple(completed),
                pending_seed_urls=tuple(seed_urls[index:]),
                paused=True,
                total_credits_used=client.budget.credits_used,
                total_requests_used=client.budget.requests_used,
            )

        query = build_query(seed_url)
        result = client.search(query, max_results=max_results)

        routable: list[NormalizedUrl] = []
        dropped: list[str] = []
        injection_signals: list[str] = []
        for url in result.urls:
            injection_signals.extend(scan_for_injection(url))
            try:
                routable.append(normalize_intake_url(url))
            except UnsupportedSourceError:
                dropped.append(url)

        completed.append(
            SeedDiscovery(
                seed_url=seed_url,
                query=query,
                discovered_urls=result.urls,
                routable=tuple(routable),
                dropped_unroutable=tuple(dropped),
                injection_signals=tuple(dict.fromkeys(injection_signals)),
                credits_used=result.credits,
                elapsed_s=result.elapsed_s,
            )
        )

    return TavilyDiscoveryRun(
        completed=tuple(completed),
        pending_seed_urls=(),
        paused=False,
        total_credits_used=client.budget.credits_used,
        total_requests_used=client.budget.requests_used,
    )
