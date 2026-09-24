"""Tavily client boundary: credential handling, budget, and discovery-only
operations -- Search and Map (Task 9.1).

Extract is deliberately not implemented: non-RunSignup extraction fallback
is out of release-1 scope (Requirement 6, owner decision 2026-09-21). Map is
implemented for completeness (design.md section 9.4) but Search is the
mechanism that actually finds sibling races (confirmed live, Task 3.3 --
Map enumerated nothing outside the seed URL's own race ID at any tested
depth). See ``tavily_discovery.py`` for the sibling-discovery orchestration.
"""

from __future__ import annotations

import json
import math
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from xc_platform.security.config import get_tavily_api_key
from xc_platform.security.fetch import FetchNetworkError, FetchRefusedError, fetch

API = "https://api.tavily.com"
TAVILY_HOSTS: frozenset[str] = frozenset({"api.tavily.com"})

DEFAULT_MAX_RESPONSE_BYTES = 2 << 20  # 2 MiB
DEFAULT_MAX_ATTEMPTS = 2  # Tavily's own /usage endpoint rate-limits aggressively
# (Task 3.3: HTTP 429 persisting 75+ seconds under modest polling) --
# retrying Tavily calls themselves is kept conservative for the same reason.
DEFAULT_RETRY_DELAY_S = 1.0


class TavilyPermanentError(RuntimeError):
    """A Tavily response will never succeed on retry."""

    def __init__(self, message: str, *, http_status: int | None = None) -> None:
        super().__init__(message)
        self.http_status = http_status


class TavilyTransientError(RuntimeError):
    """Retries were exhausted for a transient condition (429, 5xx, network)."""


def documented_credits(
    operation: str, *, units: int, instructions: bool = False
) -> int:
    """Compute credit cost from the documented rate card.

    Read from https://docs.tavily.com/documentation/api-credits on
    2026-09-20 (docs/tavily-routing-notes.md); re-read before relying on it
    in a later release. ``/usage`` is never used for metering -- confirmed
    live (Task 3.3) that its counters do not move after successful
    operations and the endpoint itself rate-limits within seconds.
    """
    if operation == "search":
        return 1  # basic search only in release 1
    if operation == "map":
        per_batch = 2 if instructions else 1
        return math.ceil(units / 10) * per_batch if units else 0
    return 0


class TavilyBudgetExhaustedError(RuntimeError):
    """A request, wall-time, or credit ceiling was reached (Requirement 6.8)."""


@dataclass
class TavilyBudget:
    """A hard ceiling shared across one discovery run.

    Distinct from :class:`~xc_platform.ingest.adapters.runsignup_client.
    RequestBudget` in tracking *credits* as well as requests -- Requirement
    6.4/6.8 name credit use as its own constraint, not implied by a request
    count (a single Map call can cost more than 1 credit).
    """

    max_requests: int
    max_credits: int
    max_wall_time_s: float
    _start: float = field(default_factory=time.monotonic, repr=False)
    requests_used: int = 0
    credits_used: int = 0

    def check(self, *, note: str = "") -> None:
        if self.requests_used >= self.max_requests:
            raise TavilyBudgetExhaustedError(
                f"request limit {self.max_requests} reached"
                + (f" before {note}" if note else "")
            )
        if self.credits_used >= self.max_credits:
            raise TavilyBudgetExhaustedError(
                f"credit limit {self.max_credits} reached"
                + (f" before {note}" if note else "")
            )
        if time.monotonic() - self._start > self.max_wall_time_s:
            raise TavilyBudgetExhaustedError(
                f"wall-time limit {self.max_wall_time_s}s reached"
                + (f" before {note}" if note else "")
            )

    def record(self, *, credits: int) -> None:
        self.requests_used += 1
        self.credits_used += credits


@dataclass(frozen=True, slots=True)
class SearchResult:
    urls: tuple[str, ...]
    credits: int
    elapsed_s: float


@dataclass(frozen=True, slots=True)
class MapResult:
    urls: tuple[str, ...]
    credits: int
    elapsed_s: float


@dataclass
class TavilyClient:
    budget: TavilyBudget
    fetcher: Callable[[str, dict[str, Any]], Any] | None = None
    max_response_bytes: int = DEFAULT_MAX_RESPONSE_BYTES
    max_attempts: int = DEFAULT_MAX_ATTEMPTS
    retry_delay_s: float = DEFAULT_RETRY_DELAY_S
    sleep: Callable[[float], None] = time.sleep

    def __post_init__(self) -> None:
        if self.fetcher is None:
            self.fetcher = self._default_fetcher

    def _default_fetcher(self, path: str, body: dict[str, Any]) -> Any:
        # Resolved per call; never stored on the instance or persisted
        # (Task 9.1: "load credentials only through the secret interface").
        api_key = get_tavily_api_key()
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }
        return fetch(
            f"{API}{path}",
            method="POST",
            body=json.dumps(body).encode("utf-8"),
            headers=headers,
            allowed_hosts=TAVILY_HOSTS,
            max_body_bytes=self.max_response_bytes,
        )

    def _call(self, path: str, body: dict[str, Any], *, note: str) -> dict[str, Any]:
        if self.fetcher is None:
            raise RuntimeError(
                "TavilyClient.fetcher is unset; __post_init__ did not run"
            )
        fetcher = self.fetcher
        last_exc: Exception | None = None
        for attempt in range(1, self.max_attempts + 1):
            self.budget.check(note=note)
            try:
                response = fetcher(path, body)
            except FetchRefusedError:
                raise  # a policy refusal is never retryable
            except FetchNetworkError as exc:
                last_exc = exc
            else:
                if response.status == 429 or 500 <= response.status < 600:
                    last_exc = TavilyTransientError(
                        f"HTTP {response.status} from {path}"
                    )
                elif response.status >= 400:
                    raise TavilyPermanentError(
                        f"HTTP {response.status} from {path}: {response.body[:500]!r}",
                        http_status=response.status,
                    )
                else:
                    try:
                        payload = json.loads(response.body)
                    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
                        raise TavilyPermanentError(
                            f"non-JSON response from {path}: {exc}",
                            http_status=response.status,
                        ) from exc
                    if not isinstance(payload, dict):
                        raise TavilyPermanentError(
                            f"unexpected JSON shape from {path}: "
                            f"{type(payload).__name__}"
                        )
                    return payload

            if attempt < self.max_attempts:
                self.sleep(self.retry_delay_s)

        raise last_exc or TavilyTransientError(
            f"exhausted {self.max_attempts} attempts for {path}"
        )

    def search(self, query: str, *, max_results: int = 10) -> SearchResult:
        """Basic search (1 credit/request). Finds sibling pages a seed
        URL/race doesn't itself reference -- confirmed live, Task 3.3."""
        started = time.monotonic()
        payload = self._call(
            "/search",
            {"query": query, "max_results": max_results, "search_depth": "basic"},
            note=f"search: {query!r}",
        )
        urls = _result_urls(payload)
        credits = documented_credits("search", units=1)
        self.budget.record(credits=credits)
        return SearchResult(
            urls=tuple(urls),
            credits=credits,
            elapsed_s=round(time.monotonic() - started, 3),
        )

    def map(
        self,
        url: str,
        *,
        max_depth: int = 1,
        limit: int = 30,
        instructions: str | None = None,
    ) -> MapResult:
        """Explore page structure from a seed URL. Not a reliable enumerator
        for RunSignup (Task 3.3) -- prefer :meth:`search` for sibling discovery."""
        started = time.monotonic()
        body: dict[str, Any] = {"url": url, "max_depth": max_depth, "limit": limit}
        if instructions:
            body["instructions"] = instructions
        payload = self._call("/map", body, note=f"map: {url}")
        urls = [str(u) for u in (payload.get("results") or [])]
        credits = documented_credits(
            "map", units=len(urls), instructions=bool(instructions)
        )
        self.budget.record(credits=credits)
        return MapResult(
            urls=tuple(urls),
            credits=credits,
            elapsed_s=round(time.monotonic() - started, 3),
        )


def _result_urls(payload: dict[str, Any]) -> list[str]:
    urls: list[str] = []
    for item in payload.get("results") or []:
        if isinstance(item, dict) and item.get("url"):
            urls.append(str(item["url"]))
        elif isinstance(item, str):
            urls.append(item)
    return urls
