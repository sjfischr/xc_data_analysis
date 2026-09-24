"""Low-level RunSignup REST client: retry, error-envelope, and request budget.

Confirmed live (Task 3.2, docs/runsignup-adapter-notes.md):

* every documented endpoint below serves athlete-level JSON without an API
  key, for a result set whose ``public_results`` flag is ``"T"``;
* **RunSignup reports errors as HTTP 200 with an ``error`` envelope in the
  body**, not as an HTTP error status -- every response body is inspected
  for an ``error`` key regardless of status code (design.md section 9.3);
* no total-count/page-count field is published; completion is a terminal
  short page, corroborated by unique ``result_id`` accounting (Task 8.3's
  caller does the accounting -- this module only pages until short/empty).
"""

from __future__ import annotations

import json
import random
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from xc_platform.security.fetch import FetchNetworkError, FetchRefusedError, fetch
from xc_platform.security.url_policy import RUNSIGNUP_FAMILY_HOSTS

BASE = "https://runsignup.com/Rest"

DEFAULT_MAX_ATTEMPTS = 5
DEFAULT_BASE_DELAY_S = 0.5
DEFAULT_MAX_DELAY_S = 8.0


class RunSignupPermanentError(RuntimeError):
    """A response will never succeed on retry: permanent 4xx or error envelope."""

    def __init__(
        self,
        message: str,
        *,
        error_code: int | None = None,
        http_status: int | None = None,
    ) -> None:
        super().__init__(message)
        self.error_code = error_code
        self.http_status = http_status


class RunSignupTransientError(RuntimeError):
    """Retries were exhausted for a transient condition (429, 5xx, network)."""


class RequestBudgetExhaustedError(RuntimeError):
    """The configured request count or wall-clock budget was exceeded."""


@dataclass
class RequestBudget:
    """A hard ceiling shared across one discovery+fetch run (Requirement 5.6).

    Not a retry-attempt counter -- each individual request the client
    successfully *sends* consumes one unit, whether it succeeds, fails
    permanently, or is retried.
    """

    max_requests: int
    max_wall_time_s: float
    _start: float = field(default_factory=time.monotonic, repr=False)
    used: int = 0

    def consume(self, *, note: str = "") -> None:
        if self.used >= self.max_requests:
            raise RequestBudgetExhaustedError(
                f"request budget of {self.max_requests} exhausted"
                + (f" at {note}" if note else "")
            )
        if time.monotonic() - self._start > self.max_wall_time_s:
            raise RequestBudgetExhaustedError(
                f"wall-time budget of {self.max_wall_time_s}s exceeded"
                + (f" at {note}" if note else "")
            )
        self.used += 1


@dataclass
class RunSignupClient:
    """Sends GET requests to the RunSignup REST API with retry and budget.

    ``fetcher`` and ``sleep``/``jitter`` are injectable so tests can run
    fully offline and deterministically (see ``test_runsignup_client.py``).
    """

    budget: RequestBudget
    fetcher: Callable[[str], Any] | None = None
    max_attempts: int = DEFAULT_MAX_ATTEMPTS
    base_delay_s: float = DEFAULT_BASE_DELAY_S
    max_delay_s: float = DEFAULT_MAX_DELAY_S
    sleep: Callable[[float], None] = time.sleep
    jitter: Callable[[], float] = random.random
    allowed_hosts: frozenset[str] = field(
        default_factory=lambda: RUNSIGNUP_FAMILY_HOSTS
    )

    def __post_init__(self) -> None:
        if self.fetcher is None:
            allowed_hosts = self.allowed_hosts
            self.fetcher = lambda url: fetch(url, allowed_hosts=allowed_hosts)

    def get_json(self, url: str, *, note: str = "") -> dict[str, Any]:
        """GET ``url``, retrying transient failures, raising on permanent ones.

        Returns the parsed JSON body on success (never an ``error``-envelope
        body -- that is always raised as :class:`RunSignupPermanentError`).
        """
        if self.fetcher is None:
            raise RuntimeError(
                "RunSignupClient.fetcher is unset; __post_init__ did not run"
            )
        fetcher = self.fetcher
        last_exc: Exception | None = None
        for attempt in range(1, self.max_attempts + 1):
            self.budget.consume(note=note or url)
            try:
                response = fetcher(url)
            except FetchRefusedError:
                raise  # a policy refusal is never retryable
            except FetchNetworkError as exc:
                last_exc = exc
            else:
                if response.status == 429 or 500 <= response.status < 600:
                    last_exc = RunSignupTransientError(
                        f"HTTP {response.status} from {url}"
                    )
                else:
                    return self._parse_success(url, response)

            if attempt < self.max_attempts:
                self._backoff_sleep(attempt)

        raise last_exc or RunSignupTransientError(
            f"exhausted {self.max_attempts} attempts for {url}"
        )

    def _parse_success(self, url: str, response: Any) -> dict[str, Any]:
        try:
            payload = json.loads(response.body)
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise RunSignupPermanentError(
                f"non-JSON response ({response.status}) from {url}: {exc}",
                http_status=response.status,
            ) from exc
        if not isinstance(payload, dict):
            raise RunSignupPermanentError(
                f"unexpected JSON shape ({type(payload).__name__}) from {url}",
                http_status=response.status,
            )
        error = payload.get("error")
        if error:
            code = error.get("error_code") if isinstance(error, dict) else None
            message = error.get("error_msg", "") if isinstance(error, dict) else ""
            # No transient RunSignup error_code has ever been observed
            # (Task 3.2: 3/301/201 were all permanent); every envelope error
            # is therefore treated as permanent -- see the module docstring.
            raise RunSignupPermanentError(
                f"RunSignup error {code}: {message}",
                error_code=code,
                http_status=response.status,
            )
        return payload

    def _backoff_sleep(self, attempt: int) -> None:
        exponential = min(self.max_delay_s, self.base_delay_s * (2 ** (attempt - 1)))
        jittered = exponential * (0.5 + self.jitter())
        self.sleep(jittered)
