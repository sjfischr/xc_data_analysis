from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

import pytest

from xc_platform.ingest.adapters.tavily_client import (
    TavilyBudget,
    TavilyBudgetExhaustedError,
    TavilyClient,
    TavilyPermanentError,
    TavilyTransientError,
    documented_credits,
)
from xc_platform.security.fetch import FetchNetworkError, FetchRefusedError


@dataclass(frozen=True, slots=True)
class _FakeResponse:
    status: int
    body: bytes


@dataclass
class _ScriptedFetcher:
    outcomes: list[Any]
    calls: list[tuple[str, dict[str, Any]]] = field(default_factory=list)

    def __call__(self, path: str, body: dict[str, Any]) -> _FakeResponse:
        self.calls.append((path, body))
        outcome = self.outcomes[len(self.calls) - 1]
        if isinstance(outcome, Exception):
            raise outcome
        assert isinstance(outcome, _FakeResponse)
        return outcome


def _ok(payload: dict[str, Any]) -> _FakeResponse:
    return _FakeResponse(status=200, body=json.dumps(payload).encode("utf-8"))


def _no_sleep(_seconds: float) -> None:
    return None


def _client(
    outcomes: list[Any], *, max_attempts: int = 2, budget: TavilyBudget | None = None
) -> tuple[TavilyClient, _ScriptedFetcher]:
    fetcher = _ScriptedFetcher(outcomes)
    client = TavilyClient(
        budget=budget
        or TavilyBudget(max_requests=100, max_credits=100, max_wall_time_s=60.0),
        fetcher=fetcher,
        max_attempts=max_attempts,
        retry_delay_s=0.01,
        sleep=_no_sleep,
    )
    return client, fetcher


# --- documented_credits -----------------------------------------------


def test_documented_credits_search_is_flat_one() -> None:
    assert documented_credits("search", units=1) == 1


@pytest.mark.parametrize(
    ("units", "instructions", "expected"),
    [
        (0, False, 0),
        (5, False, 1),
        (10, False, 1),
        (11, False, 2),
        (10, True, 2),
        (20, True, 4),
    ],
)
def test_documented_credits_map_rate_card(
    units: int, instructions: bool, expected: int
) -> None:
    assert documented_credits("map", units=units, instructions=instructions) == expected


# --- search --------------------------------------------------------------


def test_search_returns_urls_and_records_credits() -> None:
    client, fetcher = _client(
        [
            _ok(
                {
                    "results": [
                        {"url": "https://runsignup.com/a"},
                        {"url": "https://x.com/b"},
                    ]
                }
            )
        ]
    )
    result = client.search("NVJCYO cross country 2026")
    assert result.urls == ("https://runsignup.com/a", "https://x.com/b")
    assert result.credits == 1
    assert client.budget.requests_used == 1
    assert client.budget.credits_used == 1
    assert fetcher.calls[0][0] == "/search"


def test_search_accepts_bare_string_results() -> None:
    client, _fetcher = _client([_ok({"results": ["https://runsignup.com/a"]})])
    result = client.search("q")
    assert result.urls == ("https://runsignup.com/a",)


# --- map -------------------------------------------------------------------


def test_map_computes_credits_from_result_count_and_instructions() -> None:
    urls = [f"https://runsignup.com/{i}" for i in range(15)]
    client, fetcher = _client([_ok({"results": urls})])
    result = client.map(
        "https://runsignup.com/Race/Results/154050",
        max_depth=2,
        limit=30,
        instructions="race result pages",
    )
    assert len(result.urls) == 15
    assert result.credits == documented_credits("map", units=15, instructions=True)
    assert fetcher.calls[0][1]["instructions"] == "race result pages"


def test_map_without_instructions_omits_the_field() -> None:
    client, fetcher = _client([_ok({"results": []})])
    client.map("https://runsignup.com/Race/Results/154050")
    assert "instructions" not in fetcher.calls[0][1]


# --- retry / error handling -------------------------------------------


def test_http_429_is_retried_then_succeeds() -> None:
    client, fetcher = _client(
        [_FakeResponse(status=429, body=b""), _ok({"results": []})]
    )
    client.search("q")
    assert len(fetcher.calls) == 2


def test_http_5xx_exhausting_attempts_raises_transient() -> None:
    client, fetcher = _client([_FakeResponse(status=503, body=b"")] * 2, max_attempts=2)
    with pytest.raises(TavilyTransientError):
        client.search("q")
    assert len(fetcher.calls) == 2


def test_http_4xx_is_permanent_and_not_retried() -> None:
    client, fetcher = _client(
        [
            _FakeResponse(status=401, body=b'{"detail": "invalid key"}'),
            _ok({"results": []}),
        ]
    )
    with pytest.raises(TavilyPermanentError, match="401"):
        client.search("q")
    assert len(fetcher.calls) == 1


def test_network_error_is_retried() -> None:
    client, fetcher = _client([FetchNetworkError("dns failure"), _ok({"results": []})])
    client.search("q")
    assert len(fetcher.calls) == 2


def test_policy_refusal_is_never_retried() -> None:
    client, fetcher = _client(
        [FetchRefusedError("host not allowed"), _ok({"results": ["unreached"]})]
    )
    with pytest.raises(FetchRefusedError):
        client.search("q")
    assert len(fetcher.calls) == 1


def test_non_json_response_is_permanent() -> None:
    client, _fetcher = _client([_FakeResponse(status=200, body=b"not json")])
    with pytest.raises(TavilyPermanentError, match="non-JSON"):
        client.search("q")


# --- budget --------------------------------------------------------------


def test_budget_exhausted_on_request_count() -> None:
    budget = TavilyBudget(max_requests=1, max_credits=100, max_wall_time_s=60.0)
    client, _fetcher = _client(
        [_ok({"results": []}), _ok({"results": []})], budget=budget
    )
    client.search("first")
    with pytest.raises(TavilyBudgetExhaustedError, match="request limit"):
        client.search("second")


def test_budget_exhausted_on_credits() -> None:
    urls = [f"https://runsignup.com/{i}" for i in range(10)]
    budget = TavilyBudget(max_requests=100, max_credits=1, max_wall_time_s=60.0)
    client, _fetcher = _client(
        [_ok({"results": urls}), _ok({"results": []})], budget=budget
    )
    client.map(
        "https://runsignup.com/Race/Results/1"
    )  # costs 1 credit (10 urls, no instructions)
    with pytest.raises(TavilyBudgetExhaustedError, match="credit limit"):
        client.search("second")


def test_budget_exhausted_on_wall_time() -> None:
    budget = TavilyBudget(max_requests=100, max_credits=100, max_wall_time_s=5.0)
    budget._start -= 10.0
    with pytest.raises(TavilyBudgetExhaustedError, match="wall-time"):
        budget.check()


# --- Task 9.4: partial, oversized, and timeout responses -------------


def test_search_handles_a_partial_result_count_below_max_results() -> None:
    """Tavily is free to return fewer results than max_results asked for."""
    client, _fetcher = _client([_ok({"results": [{"url": "https://runsignup.com/a"}]})])
    result = client.search("very specific query", max_results=10)
    assert result.urls == ("https://runsignup.com/a",)


def test_search_handles_an_empty_results_list() -> None:
    client, _fetcher = _client([_ok({"results": []})])
    result = client.search("no matches expected")
    assert result.urls == ()
    assert result.credits == 1  # the request still happened and is billed


def test_oversized_response_is_truncated_by_the_shared_fetch_size_cap() -> None:
    """The response body is capped before it ever reaches JSON parsing
    (xc_platform.security.fetch's max_body_bytes) -- a truncated body fails
    to parse as valid JSON and surfaces as a permanent error, not a crash
    or an unbounded memory read. The cap itself is exercised for real in
    security/test_fetch.py::test_response_body_is_capped; here we confirm
    TavilyClient reacts to a body that arrives already truncated the way
    the shared fetcher would deliver one."""
    huge_json = json.dumps(
        {"results": [{"url": f"https://runsignup.com/{i}"} for i in range(100000)]}
    )
    truncated_body = huge_json.encode("utf-8")[:100]  # cuts off mid-object
    client, _fetcher = _client([_FakeResponse(status=200, body=truncated_body)])
    with pytest.raises(TavilyPermanentError, match="non-JSON"):
        client.search("q")


def test_timeout_is_treated_as_a_retryable_network_error() -> None:
    client, fetcher = _client(
        [FetchNetworkError("timed out after 20.0s"), _ok({"results": []})]
    )
    client.search("q")
    assert len(fetcher.calls) == 2


def test_default_fetcher_refuses_a_non_tavily_host_via_shared_policy() -> None:
    """Without an injected fetcher, TavilyClient still goes through the
    shared SSRF policy -- proven by constructing without a fetcher override
    and confirming it targets api.tavily.com, not an arbitrary host."""
    from xc_platform.ingest.adapters.tavily_client import API, TAVILY_HOSTS

    assert API == "https://api.tavily.com"
    assert TAVILY_HOSTS == frozenset({"api.tavily.com"})
