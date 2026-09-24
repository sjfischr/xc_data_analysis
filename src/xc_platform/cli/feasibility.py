"""Redacted feasibility harness (spec Task 3.1).

Opt-in commands for the Task 3 live probes. Safety properties:

* **Dry-run by default** — without ``--live`` no network request is made; the
  harness writes the fully-redacted request plan instead.
* **Budgets and timeouts** — a hard per-run request budget and per-request
  timeout; exhaustion stops the run in a resumable, recorded state.
* **Domain allowlist** — HTTPS-only, exact-host (or subdomain) matching;
  anything else is refused before a socket is opened.
* **Credential redaction** — every string written to disk or stdout passes
  through :mod:`xc_platform.security.redaction`; the Tavily key is read from
  the ``TAVILY_API_KEY`` environment variable and never logged.
* **Output separation** — timestamped JSON + Markdown outputs are written to
  ``feasibility/runs/<run-id>/`` (gitignored; may contain restricted source
  data). CI only ever consumes committed, reviewed fixtures under
  ``tests/fixtures/feasibility/``.

Usage (from the repo root, with ``PYTHONPATH=src``):

    python -m xc_platform.cli.feasibility probe-runsignup \
        --url "https://runsignup.com/Race/Results/154050#resultSetId-691534;perpage:100"
    python -m xc_platform.cli.feasibility probe-tavily --query "NVJCYO results"

Add ``--live`` only for an intentional, owner-approved probe (Tasks 3.2+).
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from xc_platform.security.redaction import redact_string, redact_value

DEFAULT_ALLOWED_DOMAINS = ("runsignup.com", "api.tavily.com")
DEFAULT_BUDGET_REQUESTS = 5
DEFAULT_TIMEOUT_S = 20.0
DEFAULT_OUTPUT_ROOT = Path("feasibility") / "runs"

_RUNSIGNUP_RESULTS_URL_RE = re.compile(
    r"/Race/Results/(?P<race_id>\d+).*?"
    r"(?:resultSetId-(?P<result_set_id>\d+))?"
    r"(?:;perpage:(?P<per_page>\d+))?$",
    re.IGNORECASE,
)


class HarnessError(RuntimeError):
    """Raised for refused or failed harness operations."""


@dataclass
class ProbeRequest:
    """One planned HTTP request (redacted representation is what persists)."""

    method: str
    url: str
    headers: dict[str, str] = field(default_factory=dict)
    body: dict[str, Any] | None = None
    note: str = ""


@dataclass
class HarnessConfig:
    live: bool
    budget_requests: int
    timeout_s: float
    allowed_domains: tuple[str, ...]
    output_root: Path


def utc_run_id() -> str:
    return datetime.now(tz=UTC).strftime("%Y%m%dT%H%M%SZ")


def host_allowed(url: str, allowed_domains: tuple[str, ...]) -> bool:
    parts = urlsplit(url)
    if parts.scheme != "https":
        return False
    host = (parts.hostname or "").lower()
    return any(
        host == domain or host.endswith(f".{domain}") for domain in allowed_domains
    )


def normalize_runsignup_results_url(url: str) -> dict[str, Any]:
    """Normalize a RunSignup results page URL into REST probe parameters.

    Example input: ``https://runsignup.com/Race/Results/154050`` with the
    fragment ``resultSetId-691534;perpage:100``. The REST URL shape below is
    an assumption to be verified by Task 3.2 against the current RunSignup
    API documentation.
    """
    parts = urlsplit(url)
    match = _RUNSIGNUP_RESULTS_URL_RE.search(
        parts.path + ("#" + parts.fragment if parts.fragment else "")
    )
    if not match or not match.group("race_id"):
        raise HarnessError(f"unrecognized RunSignup results URL: {redact_string(url)}")
    race_id = match.group("race_id")
    result_set_id = match.group("result_set_id")
    per_page = match.group("per_page") or "100"
    rest_url = (
        f"https://runsignup.com/Rest/race/{race_id}/results/get-results"
        f"?format=json&results_per_page={per_page}&page=1"
    )
    if result_set_id:
        rest_url += f"&individual_result_set_id={result_set_id}"
    return {
        "race_id": race_id,
        "result_set_id": result_set_id,
        "per_page": int(per_page),
        "rest_url": rest_url,
        "rest_url_assumption": (
            "REST endpoint shape unverified until Task 3.2; see "
            "https://runsignup.com/API"
        ),
    }


def plan_runsignup_probe(url: str) -> tuple[dict[str, Any], list[ProbeRequest]]:
    normalized = normalize_runsignup_results_url(url)
    requests_planned = [
        ProbeRequest(
            method="GET",
            url=f"https://runsignup.com/Rest/race/{normalized['race_id']}?format=json",
            note="race metadata",
        ),
        ProbeRequest(
            method="GET",
            url=str(normalized["rest_url"]),
            note="first results page (headers, pagination, totals)",
        ),
    ]
    return normalized, requests_planned


def plan_tavily_probe(query: str) -> tuple[dict[str, Any], list[ProbeRequest]]:
    key_present = bool(os.environ.get("TAVILY_API_KEY"))
    context = {
        "query": query,
        "api_key_source": "TAVILY_API_KEY environment variable",
        "api_key_present": key_present,
    }
    requests_planned = [
        ProbeRequest(
            method="POST",
            url="https://api.tavily.com/search",
            headers={"Authorization": "Bearer ${TAVILY_API_KEY}"},
            body={"query": query, "max_results": 5},
            note="discovery search (credits: ~1)",
        ),
    ]
    return context, requests_planned


def execute(
    config: HarnessConfig,
    requests_planned: list[ProbeRequest],
) -> list[dict[str, Any]]:
    """Run (or dry-run) the planned requests under budget/allowlist rules."""
    if len(requests_planned) > config.budget_requests:
        raise HarnessError(
            f"planned requests ({len(requests_planned)}) exceed the budget "
            f"({config.budget_requests}); raise --budget-requests explicitly"
        )
    outcomes: list[dict[str, Any]] = []
    for planned in requests_planned:
        if not host_allowed(planned.url, config.allowed_domains):
            raise HarnessError(
                "refusing non-allowlisted or non-HTTPS URL: "
                + redact_string(planned.url)
            )
        record: dict[str, Any] = {
            "method": planned.method,
            "url": redact_string(planned.url),
            "note": planned.note,
            "headers": redact_value(planned.headers),
            "body": redact_value(planned.body),
        }
        if not config.live:
            record["status"] = "dry-run (no request sent)"
            outcomes.append(record)
            continue
        record.update(_send(planned, config.timeout_s))
        outcomes.append(record)
    return outcomes


def _send(planned: ProbeRequest, timeout_s: float) -> dict[str, Any]:
    headers = dict(planned.headers)
    if "Authorization" in headers:
        key = os.environ.get("TAVILY_API_KEY", "")
        if not key:
            raise HarnessError(
                "TAVILY_API_KEY is not set; live Tavily probes need it "
                "(never store it in the repository)"
            )
        headers["Authorization"] = f"Bearer {key}"
    data = (
        json.dumps(planned.body).encode("utf-8") if planned.body is not None else None
    )
    if data is not None:
        headers.setdefault("Content-Type", "application/json")
    request = urllib.request.Request(  # noqa: S310 - allowlist enforced above
        planned.url, data=data, headers=headers, method=planned.method
    )
    started = datetime.now(tz=UTC)
    try:
        with urllib.request.urlopen(  # noqa: S310 - allowlist enforced above
            request, timeout=timeout_s
        ) as response:
            payload = response.read(1 << 20)  # cap response capture at 1 MiB
            status = int(response.status)
    except urllib.error.HTTPError as error:
        payload = error.read(1 << 16)
        status = int(error.code)
    except (urllib.error.URLError, TimeoutError) as error:
        return {
            "status": "error",
            "error": redact_string(str(error)),
            "elapsed_s": (datetime.now(tz=UTC) - started).total_seconds(),
        }
    return {
        "status": status,
        "elapsed_s": (datetime.now(tz=UTC) - started).total_seconds(),
        "response_bytes": len(payload),
        "response_preview": redact_string(
            payload[:2048].decode("utf-8", errors="replace")
        ),
    }


def write_outputs(
    config: HarnessConfig,
    probe_name: str,
    context: dict[str, Any],
    outcomes: list[dict[str, Any]],
) -> Path:
    run_id = utc_run_id()
    run_dir = config.output_root / f"{run_id}-{probe_name}"
    run_dir.mkdir(parents=True, exist_ok=True)

    document = {
        "probe": probe_name,
        "run_id": run_id,
        "generated_utc": datetime.now(tz=UTC).isoformat(timespec="seconds"),
        "mode": "live" if config.live else "dry-run",
        "budget_requests": config.budget_requests,
        "timeout_s": config.timeout_s,
        "allowed_domains": list(config.allowed_domains),
        "context": redact_value(context),
        "requests": outcomes,
    }
    json_path = run_dir / "probe.json"
    json_path.write_text(
        json.dumps(document, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )

    lines = [
        f"# Feasibility probe: {probe_name}",
        "",
        f"- Run ID: `{run_id}`",
        f"- Mode: **{document['mode']}**",
        f"- Requests planned: {len(outcomes)} (budget {config.budget_requests})",
        f"- Timeout per request: {config.timeout_s}s",
        f"- Allowed domains: {', '.join(config.allowed_domains)}",
        "",
        "| # | Method | URL | Note | Status |",
        "|---|--------|-----|------|--------|",
    ]
    for index, outcome in enumerate(outcomes, start=1):
        lines.append(
            f"| {index} | {outcome['method']} | {outcome['url']} "
            f"| {outcome['note']} | {outcome['status']} |"
        )
    lines += [
        "",
        "Raw JSON: [probe.json](probe.json). Outputs under `feasibility/runs/` ",
        "are gitignored and may contain restricted source data; CI consumes ",
        "only reviewed fixtures in `tests/fixtures/feasibility/`.",
        "",
    ]
    (run_dir / "report.md").write_text("\n".join(lines), encoding="utf-8")
    return run_dir


def load_env_file(path: Path) -> list[str]:
    """Load ``KEY=VALUE`` lines into the process environment.

    Returns the *names* loaded so a run can be audited. Values are never
    returned, logged, or echoed. Existing environment variables win, so an
    explicitly exported value is never silently overridden by a file.
    """
    if not path.exists():
        raise HarnessError(f"env file not found: {path}")
    loaded: list[str] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        name, _, value = stripped.partition("=")
        name = name.strip()
        if not name:
            continue
        os.environ.setdefault(name, value.strip().strip('"').strip("'"))
        loaded.append(name)
    return loaded


def _run_tavily_probe(args: argparse.Namespace, config: HarnessConfig) -> Path:
    """Task 3.3 driver: metered Tavily discovery and extraction."""
    from xc_platform.feasibility import runsignup, tavily
    from xc_platform.feasibility.session import ProbeSession, write_run

    run_id = utc_run_id()
    run_dir = config.output_root / f"{run_id}-tavily-discovery"
    run_dir.mkdir(parents=True, exist_ok=True)
    session = ProbeSession(
        run_dir=run_dir,
        live=config.live,
        budget_requests=config.budget_requests,
        timeout_s=config.timeout_s,
        allowed_domains=config.allowed_domains,
    )
    probe = tavily.TavilyProbe(session=session)

    findings: dict[str, Any] = {
        "account_usage_at_start": probe.usage("usage at start"),
    }
    findings["discovery"] = tavily.probe_discovery(
        probe,
        query=str(args.query),
        race_url=str(args.race_url),
        map_limit=int(args.map_limit),
        map_depth=int(args.map_depth),
    )

    # Ground truth for the extraction comparison comes from the REST adapter
    # path proven in Task 3.2, so Extract is measured against known-correct data.
    ground_truth: list[str] = []
    if config.live and args.ground_truth_set:
        event_id, set_id = (int(part) for part in str(args.ground_truth_set).split(":"))
        rows, _, _, _ = runsignup._fetch_rows(
            session,
            str(args.ground_truth_race),
            event_id,
            set_id,
            25,
            1,
            "extraction ground truth",
        )
        ground_truth = [
            f"{r.get('first_name')} {r.get('last_name')}".strip()
            for r in rows[: int(args.ground_truth_names)]
        ]
    findings["extraction"] = tavily.probe_extraction(
        probe,
        urls=list(args.extract_url or []),
        ground_truth_names=ground_truth,
        depth=str(args.extract_depth),
        label="extract RunSignup results page",
    )
    # Gate F5 proper: a genuine non-RunSignup results source.
    findings["fallback_extraction"] = tavily.probe_extraction(
        probe,
        urls=list(args.fallback_url or []),
        ground_truth_names=[],
        depth="basic",
        label="extract non-RunSignup fallback source",
    )
    discovered = (findings["discovery"]["search"].get("urls") or []) + (
        findings["discovery"]["map"].get("sample_urls") or []
    )
    findings["host_routing"] = tavily.classify_discovered_hosts(discovered)
    findings["ground_truth_source"] = {
        "race_id": str(args.ground_truth_race),
        "event_and_set": str(args.ground_truth_set),
        "names_compared": len(ground_truth),
        "note": (
            "Names come from the RunSignup REST path verified in Task 3.2 and "
            "are used only to measure extraction recall; they are not persisted."
        ),
    }
    findings["limits"] = tavily.summarize_limits(findings)
    findings["account_usage_at_end"] = probe.usage("usage at end")

    write_run(
        run_dir,
        probe="tavily-discovery",
        mode="live" if config.live else "dry-run",
        context={
            "query": str(args.query),
            "race_url": str(args.race_url),
            "extract_urls": list(args.extract_url or []),
            "credential_source": "TAVILY_API_KEY via xc_platform.security.config",
        },
        findings=findings,
        session=session,
    )
    (run_dir / "report.md").write_text(
        _render_tavily_markdown(findings, session.summary()), encoding="utf-8"
    )
    return run_dir


def _render_tavily_markdown(
    findings: dict[str, Any], session_summary: dict[str, Any]
) -> str:
    discovery = findings.get("discovery", {})
    search = discovery.get("search", {})
    mapped = discovery.get("map", {})
    extraction = findings.get("extraction", {})
    limits = findings.get("limits", {})
    start = (findings.get("account_usage_at_start") or {}).get("account") or {}
    end = (findings.get("account_usage_at_end") or {}).get("account") or {}

    lines = [
        "# Feasibility probe 3.3 -- Tavily discovery and extraction fallback",
        "",
        f"- Requests used: {session_summary['requests_used']} / "
        f"{session_summary['requests_budget']}",
        f"- Plan usage at start: {start.get('plan_usage')} / "
        f"{start.get('plan_limit')} ({start.get('current_plan')})",
        f"- Plan usage at end: {end.get('plan_usage')} / {end.get('plan_limit')}",
        f"- Credits consumed by this run: "
        f"{(end.get('plan_usage') or 0) - (start.get('plan_usage') or 0)}",
        "",
        "## Gate F4 -- discovery",
        "",
        "### Search",
        "",
        f"- Query: `{search.get('query')}`",
        f"- Results: {search.get('result_count')} "
        f"(RunSignup: {len(search.get('runsignup_urls') or [])}, "
        f"other hosts: {len(search.get('non_runsignup_urls') or [])})",
        f"- Latency: {search.get('elapsed_s')}s; credits: {search.get('credits')}",
        "",
        "### Map from the supplied race URL",
        "",
        f"- Seed: `{mapped.get('seed_url')}`",
        f"- Depth {mapped.get('max_depth')}, limit {mapped.get('limit')}",
        f"- URLs discovered: {mapped.get('urls_discovered')}; "
        f"result pages: {mapped.get('result_pages')}",
        f"- Distinct race IDs found: {mapped.get('distinct_race_ids')}",
        f"- Latency: {mapped.get('elapsed_s')}s; credits: {mapped.get('credits')}",
        "",
        "## Gate F5 -- extraction fallback",
        "",
        f"- URLs requested: {extraction.get('requested_urls')}",
        f"- Succeeded: {extraction.get('succeeded')}; failed: {extraction.get('failed')}",
        f"- Latency: {extraction.get('elapsed_s')}s; credits: {extraction.get('credits')}",
        "",
        "| URL | Chars | Names found / checked | Recall | Time pattern | Injection signals |",
        "|---|---|---|---|---|---|",
    ]
    for item in extraction.get("per_url", []):
        lines.append(
            f"| {item['url']} | {item['content_chars']} "
            f"| {item['ground_truth_names_found']} / "
            f"{item['ground_truth_names_checked']} | {item['name_recall']} "
            f"| {item['contains_time_pattern']} "
            f"| {len(item['injection_signals'])} |"
        )
    lines += [
        "",
        "## Gate F5 -- non-RunSignup fallback source",
        "",
    ]
    fallback = findings.get("fallback_extraction", {})
    if fallback.get("skipped"):
        lines.append(
            f"Skipped: {fallback['skipped']}. Fallback feasibility "
            "remains UNVERIFIED pending an owner-approved source."
        )
    else:
        lines += [
            f"- URLs: {fallback.get('requested_urls')}",
            f"- Succeeded: {fallback.get('succeeded')}; "
            f"failed: {fallback.get('failed')}",
            f"- Latency: {fallback.get('elapsed_s')}s; "
            f"credits: {fallback.get('credits')}",
            "",
            "| URL | Chars | Time pattern | Injection signals |",
            "|---|---|---|---|",
        ]
        for item in fallback.get("per_url", []):
            lines.append(
                f"| {item['url']} | {item['content_chars']} "
                f"| {item['contains_time_pattern']} "
                f"| {len(item['injection_signals'])} |"
            )

    routing = findings.get("host_routing", {})
    lines += [
        "",
        "## Host routing",
        "",
        f"- RunSignup-family URLs discovered: "
        f"{len(routing.get('runsignup_family') or [])}",
        f"- Other-host URLs discovered: {len(routing.get('other_hosts') or [])}",
        "",
        f"> {routing.get('note', '')}",
        "",
        "## Measured cost and recommended limits",
        "",
        f"- Measured credits: {limits.get('measured_credits')}",
        f"- Measured latency (s): {limits.get('measured_latency_s')}",
        "",
        "| Limit | Value |",
        "|---|---|",
    ]
    for key, value in (limits.get("recommended_limits") or {}).items():
        if key != "rationale":
            lines.append(f"| {key} | {value} |")
    lines += [
        "",
        (limits.get("recommended_limits") or {}).get("rationale", ""),
        "",
        "Raw JSON: [probe.json](probe.json). The API key is resolved through the "
        "secret interface and appears in no artifact.",
        "",
    ]
    return "\n".join(lines)


def _run_coverage_probe(args: argparse.Namespace, config: HarnessConfig) -> Path:
    """Task 3.2 driver: adaptive enumeration through a budgeted ProbeSession."""
    from xc_platform.feasibility import runsignup
    from xc_platform.feasibility.session import ProbeSession, write_run

    run_id = utc_run_id()
    run_dir = config.output_root / f"{run_id}-runsignup-coverage"
    run_dir.mkdir(parents=True, exist_ok=True)
    session = ProbeSession(
        run_dir=run_dir,
        live=config.live,
        budget_requests=config.budget_requests,
        timeout_s=config.timeout_s,
        allowed_domains=config.allowed_domains,
    )
    race_ids = list(args.race_ids or runsignup.MEET_RACE_IDS.keys())
    findings = runsignup.run(
        session,
        supplied_url=str(args.url),
        race_ids=race_ids,
        per_page=int(args.per_page),
        max_pages=int(args.max_pages),
        baseline_path=Path(args.baseline),
    )
    write_run(
        run_dir,
        probe="runsignup-coverage",
        mode="live" if config.live else "dry-run",
        context={
            "supplied_url": redact_string(str(args.url)),
            "race_ids": race_ids,
            "per_page": int(args.per_page),
            "max_pages": int(args.max_pages),
            "baseline": str(args.baseline),
        },
        findings=findings,
        session=session,
    )
    (run_dir / "report.md").write_text(
        runsignup.render_markdown(findings, session.summary()), encoding="utf-8"
    )
    return run_dir


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="xc-feasibility",
        description="Redacted, budgeted feasibility probes (dry-run by default).",
    )
    parser.add_argument(
        "--live",
        action="store_true",
        help="actually send requests (opt-in; never used by CI)",
    )
    parser.add_argument(
        "--budget-requests",
        type=int,
        default=DEFAULT_BUDGET_REQUESTS,
        help=f"max requests per run (default {DEFAULT_BUDGET_REQUESTS})",
    )
    parser.add_argument(
        "--timeout-s",
        type=float,
        default=DEFAULT_TIMEOUT_S,
        help=f"per-request timeout in seconds (default {DEFAULT_TIMEOUT_S})",
    )
    parser.add_argument(
        "--allow-domain",
        action="append",
        dest="allowed_domains",
        metavar="DOMAIN",
        help="additional exact domain to allow (repeatable)",
    )
    parser.add_argument(
        "--out-root",
        type=Path,
        default=DEFAULT_OUTPUT_ROOT,
        help="output root for run directories (default feasibility/runs)",
    )
    parser.add_argument(
        "--env-file",
        type=Path,
        default=None,
        help=(
            "load KEY=VALUE secrets into the environment for this run "
            "(values are never logged; existing variables take precedence)"
        ),
    )
    commands = parser.add_subparsers(dest="command", required=True)

    runsignup = commands.add_parser(
        "probe-runsignup", help="normalize a results URL and probe the REST API"
    )
    runsignup.add_argument(
        "--url",
        default=(
            "https://runsignup.com/Race/Results/154050#resultSetId-691534;perpage:100"
        ),
        help="RunSignup results page URL (default: the spec's sample URL)",
    )

    tavily = commands.add_parser(
        "probe-tavily", help="probe Tavily discovery for approved domains"
    )
    tavily.add_argument(
        "--query",
        default="NVJCYO cross country results",
        help="discovery query (default: NVJCYO cross country results)",
    )

    coverage = commands.add_parser(
        "probe-runsignup-coverage",
        help="Task 3.2: full event/result-set/row coverage probe (gates F1-F3)",
    )
    coverage.add_argument(
        "--url",
        default=(
            "https://runsignup.com/Race/Results/154050#resultSetId-691534;perpage:100"
        ),
        help="supplied RunSignup results URL to normalize (gate F3)",
    )
    coverage.add_argument(
        "--race-id",
        action="append",
        dest="race_ids",
        metavar="ID",
        help="race ID to enumerate (repeatable; defaults to the NVJCYO series)",
    )
    coverage.add_argument(
        "--per-page", type=int, default=500, help="rows per page (default 500)"
    )
    coverage.add_argument(
        "--max-pages",
        type=int,
        default=10,
        help="page ceiling per result set (default 10)",
    )
    coverage.add_argument(
        "--baseline",
        type=Path,
        default=Path("tests/fixtures/baseline/counts.json"),
        help="frozen baseline counts to compare against (never modified)",
    )

    discovery = commands.add_parser(
        "probe-tavily-discovery",
        help="Task 3.3: metered Tavily discovery and extraction (gates F4-F5)",
    )
    discovery.add_argument(
        "--query",
        default="NVJCYO cross country developmental meet results",
        help="discovery search query",
    )
    discovery.add_argument(
        "--race-url",
        default="https://runsignup.com/Race/Results/154050",
        help="seed URL for Map discovery",
    )
    discovery.add_argument("--map-limit", type=int, default=20)
    discovery.add_argument("--map-depth", type=int, default=1)
    discovery.add_argument(
        "--extract-url",
        action="append",
        metavar="URL",
        help="URL for Extract comparison (repeatable)",
    )
    discovery.add_argument("--ground-truth-race", default="154050")
    discovery.add_argument(
        "--ground-truth-set",
        default="877071:493637",
        metavar="EVENT_ID:SET_ID",
        help="REST result set providing ground-truth names for recall",
    )
    discovery.add_argument("--ground-truth-names", type=int, default=20)
    discovery.add_argument(
        "--extract-depth",
        choices=("basic", "advanced"),
        default="basic",
        help="extraction depth for the RunSignup page comparison",
    )
    discovery.add_argument(
        "--fallback-url",
        action="append",
        metavar="URL",
        help="non-RunSignup results source for gate F5 (repeatable)",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    config = HarnessConfig(
        live=bool(args.live),
        budget_requests=int(args.budget_requests),
        timeout_s=float(args.timeout_s),
        allowed_domains=DEFAULT_ALLOWED_DOMAINS + tuple(args.allowed_domains or ()),
        output_root=Path(args.out_root),
    )
    try:
        if args.env_file:
            names = load_env_file(Path(args.env_file))
            print(
                f"loaded {len(names)} environment name(s): {', '.join(sorted(names))}"
            )
        if args.command == "probe-tavily-discovery":
            run_dir = _run_tavily_probe(args, config)
            print(f"{'LIVE' if config.live else 'DRY-RUN'} probe written to {run_dir}")
            return 0
        if args.command == "probe-runsignup-coverage":
            run_dir = _run_coverage_probe(args, config)
            print(f"{'LIVE' if config.live else 'DRY-RUN'} probe written to {run_dir}")
            return 0
        if args.command == "probe-runsignup":
            context, requests_planned = plan_runsignup_probe(str(args.url))
            probe_name = "runsignup"
        else:
            context, requests_planned = plan_tavily_probe(str(args.query))
            probe_name = "tavily"
        outcomes = execute(config, requests_planned)
        run_dir = write_outputs(config, probe_name, context, outcomes)
    except HarnessError as error:
        print(f"refused: {error}", file=sys.stderr)
        return 2
    print(f"{'LIVE' if config.live else 'DRY-RUN'} probe written to {run_dir}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
