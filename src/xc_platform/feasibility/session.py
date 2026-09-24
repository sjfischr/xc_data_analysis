"""Budgeted, redacted HTTP session shared by every Task 3 probe.

The Task 3.1 harness (:mod:`xc_platform.cli.feasibility`) plans a fixed list of
requests up front. The Task 3.2+ probes are *adaptive* -- which result sets to
fetch is only known after the race metadata comes back -- so they need a
session object that enforces the same safety properties call by call:

* **Opt-in live mode** -- ``live=False`` performs no network I/O at all.
* **Request budget** -- a hard per-run ceiling; exhaustion raises and the run
  ends in a recorded, resumable state rather than silently truncating.
* **Domain allowlist** -- HTTPS-only, exact-host or subdomain matching.
* **Credential redaction** -- every persisted string passes through
  :mod:`xc_platform.security.redaction`.
* **Bounded capture** -- response bodies are capped and written to the run
  directory (gitignored), with a SHA-256 recorded for provenance.
"""

from __future__ import annotations

import hashlib
import json
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from xc_platform.security.redaction import redact_string, redact_value

MAX_BODY_BYTES = 8 << 20  # 8 MiB hard cap on any single captured response


class BudgetExhausted(RuntimeError):
    """Raised when a probe would exceed its configured request budget."""


class RefusedRequest(RuntimeError):
    """Raised for non-HTTPS or non-allowlisted destinations."""


@dataclass
class Call:
    """One completed (or refused/dry-run) request, as persisted to probe.json."""

    method: str
    url: str
    note: str
    status: Any
    elapsed_s: float | None = None
    response_bytes: int | None = None
    sha256: str | None = None
    body_path: str | None = None
    error: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {k: v for k, v in self.__dict__.items() if v is not None}


@dataclass
class ProbeSession:
    """Adaptive request driver with budget, allowlist, and redaction."""

    run_dir: Path
    live: bool
    budget_requests: int
    timeout_s: float = 20.0
    allowed_domains: tuple[str, ...] = ("runsignup.com", "api.tavily.com")
    min_interval_s: float = 0.25  # politeness spacing between live requests
    calls: list[Call] = field(default_factory=list)
    _last_request_at: float = 0.0

    @property
    def used(self) -> int:
        return len(self.calls)

    @property
    def remaining(self) -> int:
        return max(0, self.budget_requests - self.used)

    def _check_host(self, url: str) -> None:
        parts = urlsplit(url)
        if parts.scheme != "https":
            raise RefusedRequest(f"non-HTTPS destination: {redact_string(url)}")
        host = (parts.hostname or "").lower()
        if not any(host == d or host.endswith(f".{d}") for d in self.allowed_domains):
            raise RefusedRequest(f"host not on allowlist: {redact_string(url)}")

    def get_json(self, url: str, note: str) -> Any | None:
        """GET a JSON document. Returns None on dry-run, error, or non-JSON."""
        payload, _ = self.request("GET", url, note=note)
        if payload is None:
            return None
        try:
            return json.loads(payload)
        except (json.JSONDecodeError, UnicodeDecodeError):
            return None

    def post_json(
        self, url: str, body: dict[str, Any], note: str, headers: dict[str, str]
    ) -> Any | None:
        payload, _ = self.request("POST", url, note=note, body=body, headers=headers)
        if payload is None:
            return None
        try:
            return json.loads(payload)
        except (json.JSONDecodeError, UnicodeDecodeError):
            return None

    def request(
        self,
        method: str,
        url: str,
        *,
        note: str,
        body: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
    ) -> tuple[bytes | None, Call]:
        self._check_host(url)
        if self.remaining <= 0:
            raise BudgetExhausted(
                f"request budget of {self.budget_requests} exhausted before: {note}"
            )
        if not self.live:
            call = Call(
                method=method,
                url=redact_string(url),
                note=note,
                status="dry-run (no request sent)",
            )
            self.calls.append(call)
            return None, call

        # Politeness spacing so a probe never behaves like a scraper.
        elapsed_since = time.monotonic() - self._last_request_at
        if elapsed_since < self.min_interval_s:
            time.sleep(self.min_interval_s - elapsed_since)

        request_headers = dict(headers or {})
        data = json.dumps(body).encode("utf-8") if body is not None else None
        if data is not None:
            request_headers.setdefault("Content-Type", "application/json")
        request_headers.setdefault(
            "User-Agent", "xc-data-platform-feasibility/0.1 (Task 3 probe)"
        )
        req = urllib.request.Request(  # noqa: S310 - allowlist enforced above
            url, data=data, headers=request_headers, method=method
        )
        started = time.monotonic()
        self._last_request_at = started
        try:
            with urllib.request.urlopen(  # noqa: S310 - allowlist enforced
                req, timeout=self.timeout_s
            ) as response:
                payload = response.read(MAX_BODY_BYTES)
                status: Any = int(response.status)
        except urllib.error.HTTPError as error:
            payload = error.read(1 << 16)
            status = int(error.code)
        except (urllib.error.URLError, TimeoutError, OSError) as error:
            call = Call(
                method=method,
                url=redact_string(url),
                note=note,
                status="error",
                elapsed_s=round(time.monotonic() - started, 3),
                error=redact_string(str(error)),
            )
            self.calls.append(call)
            return None, call

        digest = hashlib.sha256(payload).hexdigest()
        body_path = self._save_body(payload, digest)
        call = Call(
            method=method,
            url=redact_string(url),
            note=note,
            status=status,
            elapsed_s=round(time.monotonic() - started, 3),
            response_bytes=len(payload),
            sha256=digest,
            body_path=body_path,
        )
        self.calls.append(call)
        return payload, call

    def _save_body(self, payload: bytes, digest: str) -> str:
        bodies = self.run_dir / "bodies"
        bodies.mkdir(parents=True, exist_ok=True)
        name = f"{self.used:03d}-{digest[:12]}.json"
        (bodies / name).write_bytes(payload)
        return f"bodies/{name}"

    def summary(self) -> dict[str, Any]:
        statuses: dict[str, int] = {}
        for call in self.calls:
            key = str(call.status)
            statuses[key] = statuses.get(key, 0) + 1
        live_calls = [c for c in self.calls if isinstance(c.elapsed_s, float)]
        return {
            "requests_used": self.used,
            "requests_budget": self.budget_requests,
            "status_counts": statuses,
            "total_elapsed_s": round(sum(c.elapsed_s or 0.0 for c in live_calls), 3),
            "slowest_s": max((c.elapsed_s or 0.0 for c in live_calls), default=0.0),
        }


def write_run(
    run_dir: Path,
    probe: str,
    mode: str,
    context: dict[str, Any],
    findings: dict[str, Any],
    session: ProbeSession,
) -> Path:
    """Persist the redacted machine-readable probe document."""
    run_dir.mkdir(parents=True, exist_ok=True)
    document = {
        "probe": probe,
        "generated_utc": datetime.now(tz=UTC).isoformat(timespec="seconds"),
        "mode": mode,
        "context": redact_value(context),
        "findings": redact_value(findings),
        "session": session.summary(),
        "calls": [redact_value(call.as_dict()) for call in session.calls],
    }
    path = run_dir / "probe.json"
    path.write_text(
        json.dumps(document, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    return path
