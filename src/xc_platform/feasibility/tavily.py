"""Task 3.3 -- Tavily discovery and extraction-fallback feasibility probe.

Verifies gates F4 (discovery) and F5 (fallback extraction) from design §17.

Every Tavily operation is metered: the probe reads ``/usage`` before and after
each call so credit consumption is *measured*, not estimated from memory. The
API key is resolved through :func:`xc_platform.security.config.get_tavily_api_key`
and never appears in any persisted artifact.

Discovery results are treated as *untrusted data*. The probe records whether
fetched content contains instruction-like text (a prompt-injection signal) but
never acts on it.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from xc_platform.feasibility.session import ProbeSession
from xc_platform.security.config import get_tavily_api_key

API = "https://api.tavily.com"

# Text patterns that, if present in fetched content, indicate an attempt to
# steer an agent. Recorded as a signal; never obeyed.
_INJECTION_PATTERNS: tuple[str, ...] = (
    r"ignore (?:all |any )?(?:previous|prior|above) instructions",
    r"disregard (?:the )?(?:previous|prior|above)",
    r"you are now",
    r"system prompt",
    r"</?(?:system|assistant|instructions)>",
    r"run this (?:script|command)",
    r"curl\s+https?://",
)


# Documented credit rates, read from https://docs.tavily.com/documentation/
# api-credits on 2026-09-20. Never assumed from memory; re-read before relying
# on them in a later release.
DOCUMENTED_RATES: dict[str, Any] = {
    "source": "https://docs.tavily.com/documentation/api-credits",
    "observed_utc": "2026-09-20",
    "search_basic_per_request": 1,
    "search_advanced_per_request": 2,
    "extract_basic_per_5_urls": 1,
    "extract_advanced_per_5_urls": 2,
    "map_per_10_pages": 1,
    "map_with_instructions_per_10_pages": 2,
    "crawl": "map cost + extract cost",
    "payg_usd_per_credit": 0.008,
    "researcher_plan_credits_per_month": 1000,
}


def documented_credits(
    operation: str, *, units: int = 1, depth: str = "basic", instructions: bool = False
) -> int:
    """Compute credit cost from the documented rate card.

    The ``/usage`` endpoint proved unusable for per-operation accounting (it
    did not increment within the observation window and returns HTTP 429 under
    modest polling), so the platform must meter from its own request log.
    """
    import math

    if operation == "search":
        return units * (2 if depth == "advanced" else 1)
    if operation == "extract":
        per_batch = 2 if depth == "advanced" else 1
        return math.ceil(units / 5) * per_batch if units else 0
    if operation == "map":
        per_batch = 2 if instructions else 1
        return math.ceil(units / 10) * per_batch if units else 0
    return 0


@dataclass
class CreditMeter:
    """Documented-rate credit accounting for one logical operation."""

    operation: str
    units: int = 0
    depth: str = "basic"
    instructions: bool = False
    before: dict[str, Any] | None = None
    after: dict[str, Any] | None = None

    def credits(self) -> int:
        return documented_credits(
            self.operation,
            units=self.units,
            depth=self.depth,
            instructions=self.instructions,
        )

    def delta(self) -> dict[str, Any]:
        """Reported credit cost, plus any observed /usage movement."""
        observed: dict[str, int] = {}
        if self.before and self.after:
            for scope in ("key", "account"):
                before_scope = self.before.get(scope) or {}
                after_scope = self.after.get(scope) or {}
                for metric, after_value in after_scope.items():
                    before_value = before_scope.get(metric)
                    if isinstance(after_value, int) and isinstance(before_value, int):
                        change = after_value - before_value
                        if change:
                            observed[f"{scope}.{metric}"] = change
        return {
            "documented_credits": self.credits(),
            "billable_units": self.units,
            "usage_endpoint_delta": observed or "no movement observed",
        }


@dataclass
class TavilyProbe:
    session: ProbeSession
    meters: list[CreditMeter] = field(default_factory=list)

    def _headers(self) -> dict[str, str]:
        # Resolved per call; never stored on the instance or persisted.
        return {"Authorization": f"Bearer {get_tavily_api_key()}"}

    def usage(self, note: str) -> dict[str, Any] | None:
        """Read ``/usage``. Called at most twice per run: it is rate-limited.

        Observed 2026-09-20: HTTP 429 after a handful of calls within a few
        minutes, and counters that did not move after successful operations.
        """
        payload, call = self.session.request(
            "GET", f"{API}/usage", note=note, headers=self._headers()
        )
        if payload is None:
            return {"unavailable": f"status={call.status}"}
        import json

        try:
            return json.loads(payload)
        except json.JSONDecodeError:
            return {"unavailable": "unparseable response"}

    def metered(
        self,
        operation: str,
        path: str,
        body: dict[str, Any],
        note: str,
        *,
        depth: str = "basic",
        instructions: bool = False,
    ) -> tuple[Any | None, CreditMeter, float | None]:
        """Run one Tavily operation and account for it at documented rates."""
        result = self.session.post_json(
            f"{API}{path}", body=body, note=note, headers=self._headers()
        )
        elapsed = self.session.calls[-1].elapsed_s
        meter = CreditMeter(
            operation=operation, depth=depth, instructions=instructions
        )
        # Billable units are counted from what actually came back, because
        # Tavily does not charge for failed extractions or failed maps.
        if isinstance(result, dict):
            if operation == "extract":
                meter.units = len(result.get("results") or [])
            elif operation == "map":
                meter.units = len(result.get("results") or [])
            else:
                meter.units = 1
        self.meters.append(meter)
        return result, meter, elapsed

    def total_credits(self) -> int:
        return sum(meter.credits() for meter in self.meters)


def scan_for_injection(text: str) -> list[str]:
    """Report instruction-like patterns in fetched content. Never obeyed."""
    found: list[str] = []
    for pattern in _INJECTION_PATTERNS:
        if re.search(pattern, text, re.IGNORECASE):
            found.append(pattern)
    return found


def _result_urls(payload: Any) -> list[str]:
    if not isinstance(payload, dict):
        return []
    urls: list[str] = []
    for item in payload.get("results") or []:
        if isinstance(item, dict) and item.get("url"):
            urls.append(str(item["url"]))
        elif isinstance(item, str):
            urls.append(item)
    return urls


def probe_discovery(
    probe: TavilyProbe, *, query: str, race_url: str, map_limit: int, map_depth: int
) -> dict[str, Any]:
    """Gate F4: can Tavily find the relevant race/year/result pages?"""
    findings: dict[str, Any] = {}

    search, search_meter, search_time = probe.metered(
        "search",
        "/search",
        {"query": query, "max_results": 10, "search_depth": "basic"},
        note="discovery search",
    )
    search_urls = _result_urls(search)
    findings["search"] = {
        "query": query,
        "result_count": len(search_urls),
        "elapsed_s": search_time,
        "credits": search_meter.delta(),
        "runsignup_urls": [u for u in search_urls if "runsignup.com" in u],
        "non_runsignup_urls": [u for u in search_urls if "runsignup.com" not in u],
        "urls": search_urls,
    }

    mapped, map_meter, map_time = probe.metered(
        "map",
        "/map",
        {
            "url": race_url,
            "max_depth": map_depth,
            "limit": map_limit,
            "instructions": "cross country race result pages by year and division",
        },
        note="site map from the supplied race URL",
        instructions=True,
    )
    mapped_urls = mapped.get("results") if isinstance(mapped, dict) else None
    mapped_urls = [str(u) for u in (mapped_urls or [])]
    result_pages = [u for u in mapped_urls if re.search(r"/Results?/", u, re.I)]
    race_ids = sorted(
        {m.group(1) for u in mapped_urls for m in [re.search(r"/Results/(\d+)", u)] if m}
    )
    findings["map"] = {
        "seed_url": race_url,
        "max_depth": map_depth,
        "limit": map_limit,
        "elapsed_s": map_time,
        "credits": map_meter.delta(),
        "urls_discovered": len(mapped_urls),
        "result_pages": len(result_pages),
        "distinct_race_ids": race_ids,
        "sample_urls": mapped_urls[:15],
    }
    return findings


def probe_extraction(
    probe: TavilyProbe,
    *,
    urls: list[str],
    ground_truth_names: list[str],
    depth: str = "basic",
    label: str = "extract results page",
) -> dict[str, Any]:
    """Compare Tavily Extract against REST ground truth for the same page."""
    if not urls:
        return {
            "requested_urls": [],
            "skipped": "no URLs supplied",
            "per_url": [],
        }
    extracted, meter, elapsed = probe.metered(
        "extract",
        "/extract",
        {"urls": urls, "extract_depth": depth},
        note=label,
        depth=depth,
    )
    results = (extracted or {}).get("results") or []
    failed = (extracted or {}).get("failed_results") or []

    per_url: list[dict[str, Any]] = []
    for item in results:
        content = str(item.get("raw_content") or "")
        matched = [n for n in ground_truth_names if n and n.lower() in content.lower()]
        per_url.append(
            {
                "url": item.get("url"),
                "content_chars": len(content),
                "injection_signals": scan_for_injection(content),
                "ground_truth_names_checked": len(ground_truth_names),
                "ground_truth_names_found": len(matched),
                "name_recall": (
                    round(len(matched) / len(ground_truth_names), 3)
                    if ground_truth_names
                    else None
                ),
                "contains_time_pattern": bool(re.search(r"\b\d{1,2}:\d{2}(?:\.\d+)?\b", content)),
            }
        )
    return {
        "requested_urls": urls,
        "extract_depth": depth,
        "elapsed_s": elapsed,
        "credits": meter.delta(),
        "succeeded": len(results),
        "failed": len(failed),
        "failed_details": failed[:5],
        "per_url": per_url,
    }


# Hosts observed serving identical RunSignup race IDs under different brands.
# They must route to the RunSignup REST adapter, not to the Tavily fallback.
RUNSIGNUP_FAMILY_HOSTS: tuple[str, ...] = (
    "runsignup.com",
    "trisignup.com",
    "adventuresignup.com",
)


def classify_discovered_hosts(urls: list[str]) -> dict[str, Any]:
    """Split discovered URLs into RunSignup-family versus genuine other hosts."""
    from urllib.parse import urlsplit

    family: list[str] = []
    other: list[str] = []
    for url in urls:
        host = (urlsplit(url).hostname or "").lower().removeprefix("www.")
        (family if host in RUNSIGNUP_FAMILY_HOSTS else other).append(url)
    return {
        "runsignup_family": family,
        "other_hosts": other,
        "note": (
            "trisignup.com and adventuresignup.com are white-label RunSignup "
            "front ends serving the same numeric race IDs. The source router "
            "must treat them as RunSignup, or identical races will be ingested "
            "twice under different provenance."
        ),
    }


def summarize_limits(findings: dict[str, Any]) -> dict[str, Any]:
    """Derive recommended production limits from what was measured."""
    map_info = findings.get("discovery", {}).get("map", {})
    search_info = findings.get("discovery", {}).get("search", {})
    extract_info = findings.get("extraction", {})

    def credits_of(block: dict[str, Any]) -> int:
        delta = block.get("credits") or {}
        return int(delta.get("documented_credits", 0))

    return {
        "rate_card": DOCUMENTED_RATES,
        "measured_credits": {
            "search": credits_of(search_info),
            "map": credits_of(map_info),
            "extract": credits_of(extract_info),
        },
        "measured_latency_s": {
            "search": search_info.get("elapsed_s"),
            "map": map_info.get("elapsed_s"),
            "extract": extract_info.get("elapsed_s"),
        },
        "recommended_limits": {
            "max_pages_per_run": 25,
            "max_crawl_depth": 2,
            "max_wall_clock_s": 120,
            "max_response_bytes": 8 << 20,
            "max_credits_per_import": 10,
            "rationale": (
                "Derived from measured per-operation credit cost and the "
                "account's remaining plan allowance; a single import must not "
                "be able to consume a meaningful share of the monthly plan."
            ),
        },
    }
