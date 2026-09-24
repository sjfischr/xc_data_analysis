from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

import pytest

from xc_platform.ingest.adapters.runsignup_client import (
    RequestBudget,
    RequestBudgetExhaustedError,
    RunSignupClient,
    RunSignupPermanentError,
    RunSignupTransientError,
)
from xc_platform.security.fetch import FetchNetworkError, FetchRefusedError


@dataclass(frozen=True, slots=True)
class _FakeResponse:
    status: int
    body: bytes


@dataclass
class _ScriptedFetcher:
    """Returns/raises a scripted sequence of outcomes, one per call."""

    outcomes: list[Any]
    calls: list[str] = field(default_factory=list)

    def __call__(self, url: str) -> _FakeResponse:
        self.calls.append(url)
        outcome = self.outcomes[len(self.calls) - 1]
        if isinstance(outcome, Exception):
            raise outcome
        assert isinstance(outcome, _FakeResponse)
        return outcome


def _ok(payload: dict[str, Any]) -> _FakeResponse:
    return _FakeResponse(status=200, body=json.dumps(payload).encode("utf-8"))


def _no_sleep(_seconds: float) -> None:
    return None


def _no_jitter() -> float:
    return 0.0


def _client(
    outcomes: list[Any], *, max_attempts: int = 5, budget: RequestBudget | None = None
) -> tuple[RunSignupClient, _ScriptedFetcher]:
    fetcher = _ScriptedFetcher(outcomes)
    client = RunSignupClient(
        budget=budget or RequestBudget(max_requests=100, max_wall_time_s=60.0),
        fetcher=fetcher,
        max_attempts=max_attempts,
        base_delay_s=0.01,
        max_delay_s=0.02,
        sleep=_no_sleep,
        jitter=_no_jitter,
    )
    return client, fetcher


def test_successful_response_returns_parsed_json() -> None:
    client, fetcher = _client([_ok({"race": {"name": "Test"}})])
    result = client.get_json("https://runsignup.com/Rest/race/1")
    assert result == {"race": {"name": "Test"}}
    assert len(fetcher.calls) == 1


def test_error_envelope_at_http_200_raises_permanent_immediately() -> None:
    client, fetcher = _client(
        [
            _ok({"error": {"error_code": 201, "error_msg": "Race not found."}}),
            _ok({"race": {"name": "should never be reached"}}),
        ]
    )
    with pytest.raises(RunSignupPermanentError, match="201") as excinfo:
        client.get_json("https://runsignup.com/Rest/race/999999999")
    assert excinfo.value.error_code == 201
    # No retry for a permanent envelope error.
    assert len(fetcher.calls) == 1


def test_http_429_is_retried_then_succeeds() -> None:
    client, fetcher = _client(
        [_FakeResponse(status=429, body=b""), _ok({"race": {"name": "Test"}})]
    )
    result = client.get_json("https://runsignup.com/Rest/race/1")
    assert result == {"race": {"name": "Test"}}
    assert len(fetcher.calls) == 2


def test_http_5xx_is_retried_then_succeeds() -> None:
    client, fetcher = _client(
        [
            _FakeResponse(status=503, body=b""),
            _FakeResponse(status=502, body=b""),
            _ok({"race": {"name": "Test"}}),
        ]
    )
    result = client.get_json("https://runsignup.com/Rest/race/1")
    assert result == {"race": {"name": "Test"}}
    assert len(fetcher.calls) == 3


def test_transient_failures_exhausting_attempts_raises() -> None:
    client, fetcher = _client([_FakeResponse(status=503, body=b"")] * 3, max_attempts=3)
    with pytest.raises(RunSignupTransientError):
        client.get_json("https://runsignup.com/Rest/race/1")
    assert len(fetcher.calls) == 3


def test_network_error_is_retried() -> None:
    client, fetcher = _client(
        [FetchNetworkError("dns failure"), _ok({"race": {"name": "Test"}})]
    )
    result = client.get_json("https://runsignup.com/Rest/race/1")
    assert result == {"race": {"name": "Test"}}
    assert len(fetcher.calls) == 2


def test_policy_refusal_is_never_retried() -> None:
    client, fetcher = _client(
        [FetchRefusedError("host not allowed"), _ok({"race": {"name": "unreached"}})]
    )
    with pytest.raises(FetchRefusedError):
        client.get_json("https://evil.example.com/")
    assert len(fetcher.calls) == 1


def test_non_json_response_raises_permanent() -> None:
    client, fetcher = _client(
        [_FakeResponse(status=200, body=b"<html>not json</html>")]
    )
    with pytest.raises(RunSignupPermanentError, match="non-JSON"):
        client.get_json("https://runsignup.com/Rest/race/1")


def test_request_budget_is_shared_across_calls_and_raises_when_exhausted() -> None:
    budget = RequestBudget(max_requests=2, max_wall_time_s=60.0)
    client, fetcher = _client(
        [_ok({"a": 1}), _ok({"b": 2}), _ok({"c": 3})], budget=budget
    )
    client.get_json("https://runsignup.com/Rest/a")
    client.get_json("https://runsignup.com/Rest/b")
    with pytest.raises(RequestBudgetExhaustedError):
        client.get_json("https://runsignup.com/Rest/c")
    assert budget.used == 2


def test_wall_time_budget_is_enforced() -> None:
    budget = RequestBudget(max_requests=100, max_wall_time_s=5.0)
    # Force the budget's start reference far enough into the past that the
    # wall-time ceiling is already exceeded, without needing to monkeypatch
    # the monotonic clock itself.
    budget._start -= 10.0
    with pytest.raises(RequestBudgetExhaustedError, match="wall-time"):
        budget.consume()


def test_default_fetcher_is_wired_to_the_configured_allowed_hosts() -> None:
    """Without an injected fetcher, the client must still refuse non-family hosts."""
    client = RunSignupClient(budget=RequestBudget(max_requests=5, max_wall_time_s=10.0))
    with pytest.raises(FetchRefusedError):
        client.get_json("https://evil.example.com/")
