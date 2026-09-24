# Feasibility workspace (spec Task 3)

Reproducible feasibility probes, their reports, and (eventually) the Task 3.8
go/no-go package live here.

## Layout

- `runs/` — **gitignored.** Timestamped output of the redacted feasibility
  harness (`probe.json` + `report.md` per run). May contain restricted source
  data; never committed, never read by CI.
- Committed, reviewed probe fixtures for CI belong in
  [tests/fixtures/feasibility/](../tests/fixtures/feasibility/) instead.

## Running the harness

Dry-run (default — no network traffic, writes the redacted request plan):

```powershell
$env:PYTHONPATH = "src"
python -m xc_platform.cli.feasibility probe-runsignup
python -m xc_platform.cli.feasibility probe-tavily --query "NVJCYO results"
```

Live probes (Tasks 3.2+) are **opt-in** via `--live`, subject to request
budgets (`--budget-requests`), per-request timeouts (`--timeout-s`), and an
HTTPS-only exact-domain allowlist (`--allow-domain` to extend). The Tavily
key comes from the `TAVILY_API_KEY` environment variable only, and all output
is redacted via `xc_platform.security.redaction`. Ordinary tests and CI never
invoke live endpoints (spec execution rules).
