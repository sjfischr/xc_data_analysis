# XC Data — NVJCYO Cross Country

Results, standings, and athlete progress for the NVJCYO cross-country
developmental meets (2023 onward), with an AI assistant that answers
questions about the data.

The platform runs on AWS:

- **Site**: a Next.js static site on CloudFront, with invite-only Cognito
  sign-in.
- **API**: FastAPI on App Runner.
- **Data**: SQLite snapshots published to S3.
- **Assistant**: Claude on Amazon Bedrock through AgentCore.

The original Streamlit dashboard on Heroku keeps running until the new site
is accepted ([see below](#legacy-streamlit-dashboard)).

## What it does

- **Overview.** Filter by season, school, division, gender, meet, and grade.
  The page shows:
  - headline metrics and Saint Sebastian leaders;
  - fastest pace per mile and top placements;
  - most improved athletes;
  - cross-country team scoring, with a chart.
- **Athletes.** A searchable directory, and a profile for each athlete:
  - school and grade by season, and career bests;
  - time, pace, speed, and placement charts with trendlines;
  - a pace trend that states its confidence;
  - every race result.
- **Schools.** Participation by season, team-finish history, fastest
  athletes, and the roster by grade.
- **Standings.** The Saint Sebastian Award tracker (top 3, or full lists by
  school), and team scores by meet.
- **Ask the Data.** Plain-English questions, answered from the data with
  cited sources and follow-up suggestions. Answers can include:
  - interactive charts and tables;
  - exact calculations;
  - head-to-head comparisons;
  - what-if team scoring;
  - statistics run in a sandboxed Python environment (scipy, statsmodels).

  Admins always have access; other users need the Cognito `agent-users`
  group. An SSM switch turns it off for everyone.
- **Admin intake.** Paste a RunSignup results URL, review what was found,
  settle identity questions ("is this the same athlete or school?"), and
  publish. Nothing is published without an administrator's commit, and
  historical seasons (2023–2025) are frozen.
- **Corrections.** Admins can rename a school. Results stay attached, and the
  change is recorded with who made it and why.

## Architecture

```text
Browser ──► CloudFront + S3 (Next.js static export, web/)
   │
   └─ cookie session ──► App Runner: FastAPI (src/xc_platform/api)
                           │   reads ──► verified SQLite snapshot from S3
                           │   admin ──► single writer DB ──► publish new snapshot to S3
                           └── chat (SSE) ──IAM──► AgentCore Runtime (analytics agent)
                                                     ├── Bedrock (Claude Haiku 4.5)
                                                     ├── AgentCore Memory (per-user history)
                                                     └── Code Interpreter (sandboxed Python)
```

Key properties:

- **Every answer is pinned to one data version.** Snapshots are immutable
  and SHA-256-verified, and are activated by compare-and-swap.
- **Only the API can invoke the agent.** The API passes the agent a
  pseudonymous user ID; the agent never sees raw identities.
- **Model-written code never runs on our servers.** It runs only in the
  isolated Code Interpreter sandbox, which has no network access and no
  credentials.

## Repository layout

| Path | What it is |
|---|---|
| `src/xc_platform/` | The platform (Python 3.12). Subpackages: `api`, `agents`, `analytics`, `ingest`, `resolution`, `db` (schema access and S3 publication), `migration` (historical backfill), `security` |
| `migrations/` | Versioned SQL schema, including team-score and Saint Sebastian views |
| `web/` | Next.js site (TypeScript, Tailwind, Vega-Lite) |
| `infrastructure/cdk/` | AWS CDK app: Storage, Web, Agent, and Ops stacks |
| `scripts/` | Agent evaluation, snapshot publishing, and dependency and link checks |
| `tests/` | Parity, contract, security, and live-integration tests (unit tests live beside the code in `src/`) |
| `docs/` | Evidence, runbooks, the dependency review, and the change report |
| `.kiro/specs/xc-data-platform/` | Requirements, design, and the task record, with dated evidence |
| `dashboard.py`, `run_parser.py`, … | Legacy Streamlit pipeline and dashboard |

## Local development

**Backend.** Python 3.12; install from the hashed lockfile:

```bash
python -m venv .venv
.venv/Scripts/pip install --require-hashes -r requirements.lock   # Windows
# .venv/bin/pip install --require-hashes -r requirements.lock     # Linux/macOS/WSL

python -m pytest src tests -m "not live and not integration"      # unit and parity tests
python -m ruff check src && python -m mypy src/xc_platform        # lint and types
```

**Run the API and site locally:**

```bash
python -m xc_platform.cli.run_local_api        # API on http://localhost:8000 (starts with an empty database)
cd web && pnpm install --frozen-lockfile && pnpm dev   # site on http://localhost:3000
```

- Locally, the sign-in page offers a development sign-in form instead of
  Cognito.
- Ask the Data and Admin intake call real AWS services (Bedrock, RunSignup),
  so they need AWS credentials.
- Node 20.19.1 and pnpm 10.26.1 are pinned.

**Infrastructure** (synthesis and tests only; no AWS changes):

```bash
cd infrastructure/cdk
python -m venv .venv && .venv/Scripts/pip install -r requirements.txt
.venv/Scripts/python -m pytest -q
npx aws-cdk@2.1032.0 synth
```

**Agent evaluation.** Live on Bedrock, about $0.25 per run:

```bash
python scripts/agent_eval.py --python-sandbox
```

It runs 25 cases whose ground truth is computed from the database. Re-run it
after any prompt, tool, or model change. Results so far are in
[docs/model-evaluation-evidence.md](docs/model-evaluation-evidence.md).

## Deploying

Deploys run from WSL with the owner's AWS credentials. The exact steps are
in [docs/task-19-deploy-runbook.md](docs/task-19-deploy-runbook.md). The
agent runtime deploys in two passes, because its image must exist before the
runtime can reference it.

| Stack | Contains |
|---|---|
| `XcPlatform-Storage` | Data bucket |
| `XcPlatform-Web` | Cognito, ECR, the App Runner API, and the CloudFront site |
| `XcPlatform-Agent` | AgentCore runtime, Memory, and Code Interpreter |
| `XcPlatform-Ops` | Alarms and the Bedrock and account budgets |

## Secret and dependency hygiene

Credentials are never stored in the repository. They are read from
environment variables locally and from SSM in AWS, through
`src/xc_platform/security/config.py`. Logs and traces are scrubbed by
`src/xc_platform/security/redaction.py`.

Enable the secret-scanning hook once per clone:

```bash
pip install pre-commit==4.0.1
pre-commit install
```

| Check | Runs |
| --- | --- |
| gitleaks v8.21.2 secret scan (redacted output) | pre-commit on staged changes, CI on full history |
| Dependency pinning and lockfile hash coverage | pre-commit and CI (`scripts/check_dependency_pins.py`) |
| `pip-audit` on the hashed lockfiles | CI |
| `pnpm audit` and frozen-lockfile check | CI (`web/`) |
| Log-redaction tests | CI |

Dependencies are pinned exactly and locked with hashes. The agent image uses
`requirements-agent.lock`, a slim subset with identical pins. Adding or
upgrading a package is a reviewed change: see
[docs/dependency-review.md](docs/dependency-review.md), which also records the
rollback path for each control.

## Documentation

- [docs/modernization-change-report.md](docs/modernization-change-report.md):
  what the AWS modernization changed, what was fixed, and open items.
- [docs/task-19-deploy-runbook.md](docs/task-19-deploy-runbook.md): deploy,
  access control, alarms, and intake steps.
- [docs/operations-and-acceptance-readiness.md](docs/operations-and-acceptance-readiness.md):
  runbooks and architecture decisions.
- [docs/model-evaluation-evidence.md](docs/model-evaluation-evidence.md):
  model choice and agent evaluation.
- `.kiro/specs/xc-data-platform/tasks.md`: the task-by-task record.

## Legacy Streamlit dashboard

The original pipeline (`run_parser.py`) and dashboard (`dashboard.py`) are
still deployed on Heroku as the production fallback. They will be retired
only after the new site is accepted (Task 18.3). Until then, they are kept
unchanged, as provenance for the 2023–2025 data. To run the dashboard
locally:

```bash
streamlit run dashboard.py
```

## Ideas for later

- Forecast race times, and flag likely "breakout" runners early.
- Bring in weather and course elevation to explain race-to-race variation.
- Performance tiers (clustering) for developmental versus competitive
  runners.
- A per-user admin toggle for Ask the Data access. Today, access is set
  through the Cognito `agent-users` group.
