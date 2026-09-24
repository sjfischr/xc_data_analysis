# Change report: AWS platform modernization

**Branch:** `feature/aws-platform-modernization` (from `master` at `65bf1be`)
**Date:** 2026-09-24
**Spec:** `.kiro/specs/xc-data-platform/` (requirements, design, and a
task-by-task record in `tasks.md` with dated evidence for every claim)

## Summary

This branch replaces the Streamlit dashboard on Heroku with a new platform
on AWS. The Heroku dashboard keeps running unchanged until the new site is
accepted (Task 18.3).

| | Before (`master`) | After (this branch) |
|---|---|---|
| Data | One CSV rebuilt by scripts | Normalized SQLite, versioned migrations, immutable S3 snapshots |
| Site | Streamlit on Heroku, no sign-in | Next.js static site on CloudFront, Cognito invite-only sign-in |
| API | None | FastAPI on App Runner, cookie sessions with CSRF protection |
| New results | Manual parsing scripts | Admin intake from a RunSignup URL, with review and publish |
| Questions | None | "Ask the Data" agent: Claude on Bedrock via AgentCore, with charts and sandboxed Python |
| Infrastructure | Heroku dyno | AWS CDK: four stacks, all synthesized and unit-tested |

**Size:** 350 files and about 73,700 added lines, including lockfiles, test fixtures and the spec. The main areas:

| Area | Files | Lines |
|---|---|---|
| Python platform (`src/xc_platform`) | 185 | 29.7k |
| Tests (`tests/`, plus tests beside the code) | — | 14.3k in `tests/`, plus unit tests |
| Web app (`web/`) | 19 | 4.7k |
| Infrastructure as code | 6 stacks | 0.8k |
| Spec and docs | 19 | 5.8k |

**Verification:** 521 platform tests, 63 top-level tests and 23 infrastructure
tests pass. Live integration tests (real S3, RunSignup, Tavily) are separate
and opt-in.

## What was built, by area

### Foundations (Tasks 1–3)

- **Reproducible dependencies.**
  - `requirements.in` → hashed `requirements.lock`.
  - A separate slim `requirements-agent.lock` for the agent image.
  - pnpm lockfile for the web app.
  - Every package addition is recorded in `docs/dependency-review.md`.
- **Secret hygiene.**
  - gitleaks pre-commit and CI (`.gitleaks.toml`, `.pre-commit-config.yaml`).
  - Secrets come only from environment variables or SSM
    (`security/config.py`).
  - Logs are redacted (`security/redaction.py`).
- **CI workflows** (`.github/workflows/`): dependency hygiene and continuous
  validation.
- **Feasibility program** (`feasibility/`, `docs/*-evidence.md`): AgentCore
  runtime, S3 publication protocol, RunSignup and Tavily sources, Cognito
  sessions, and model evaluation. Each was proved live before production code
  depended on it.

### Data model, migration, publication (Tasks 4–6)

- **Normalized schema** with versioned migrations (`migrations/0001-0003`):
  meets, races, schools, athletes, season rosters, results, aliases, the
  ingest pipeline, resolution cases and decisions, and publication lineage.
  Team scoring and Saint Sebastian standings are SQL views.
- **Historical backfill of 2023–2025** (`migration/`): 4,203 results with
  **zero discrepancies** against the frozen legacy baseline
  (`tests/fixtures/baseline/`, `migration/parity.py`).
- **S3 publication** (`db/publication/`):
  - Immutable snapshots, SHA-256 and SQLite verification on read.
  - Compare-and-swap activation, and a lease so there is one writer at a time.
  - Restore, and fallback to the last good snapshot.

### Analytics (Tasks 7, 19.1, 19.2)

- `analytics/` holds:
  - catalog;
  - progression, with a versioned OLS trend and statistical safeguards;
  - team scores and standings;
  - dashboard aggregates: overview, leaderboards, athlete and school
    profiles;
  - scenarios: head-to-head comparison, and what-if team scoring that exactly
    matches the official scores on all 47 real races;
  - a bounded read-only SQL tool.

### Ingestion and entity resolution (Tasks 8–12, 19.3)

- **RunSignup adapter and source router**, with request budgets. Tavily
  discovery exists with credit controls; extraction fallback is out of scope.
- **Deterministic resolution**:
  - exact and alias matching;
  - candidate scoring with hard conflicts;
  - a human review queue with audited decisions;
  - reversible athlete merges.
- **End-to-end intake workflow:** discover → stage → resolve → commit →
  publish. Frozen seasons are refused, and nothing publishes without an
  explicit commit.

### API (Task 13)

- FastAPI, versioned under `/api/v1`. Every read pins one data version and
  reports it.
- **Sessions:** server-side cookie sessions, double-submit CSRF, and
  Cognito hosted-UI login with callback redirect.
- **Admin endpoints:** intake, review, publish, and school corrections.
- **Chat:** Ask the Data over Server-Sent Events.

### Web app (Task 14, 19.1–19.3)

- Next.js static export with a design system: light and dark tokens, cards,
  stat tiles, tables and Vega-Lite charts.
- **Pages:**
  - **Overview**: filters, headline metrics, Saint Sebastian leaders, fastest
    pace, top placements, most improved, and team scoring with a chart.
  - **Athletes**: directory and profile, with pace, time, speed and placement
    charts including trendlines.
  - **Schools**: directory and profile, with season charts, team-finish
    history, fastest athletes and roster by grade.
  - **Standings**: Saint Sebastian tracker and team scores.
  - **Ask**: the agent chat.
  - **Admin**: intake and review.
- **Parity:** every feature of the legacy `dashboard.py` is present (checklist
  in `tasks.md` 19.1).

### Ask the Data agent (Tasks 11, 19.2)

- **15 tools**:
  - typed data tools;
  - a schema-described SQL tool;
  - an exact calculator;
  - a constrained chart builder (the model never writes a chart spec);
  - comparison and what-if tools;
  - Python in an isolated AgentCore Code Interpreter with scipy and
    statsmodels, no network and no credentials.
- **Streamed events:** text, tool steps, charts, images, code, follow-ups,
  and provenance.
- **Memory:** conversation memory in AgentCore Memory, pseudonymous per
  user, deletable by the user.
- **Access:**
  - Only the API can invoke the runtime (IAM).
  - Per-user access through the Cognito `agent-users` group; admins always
    have access.
  - A global off switch in SSM.
- **Evaluation** (`scripts/agent_eval.py`, 25 ground-truth cases):
  - Haiku 4.5 passed 25/25 at a median 4.9 s and about $0.01 per question.
  - Sonnet 4.6 passed 25/25 at 9.7 s and about $0.03.
  - Haiku is the default. Details are in `docs/model-evaluation-evidence.md`.

### Infrastructure (Tasks 15, 18, 19)

`infrastructure/cdk/` holds four stacks.

| Stack | Contents |
|---|---|
| Storage | Data bucket |
| Web | Cognito, ECR, App Runner API, S3 + CloudFront site with a clean-URL function |
| Agent | AgentCore runtime, Memory, sandboxed Code Interpreter, least-privilege role |
| Ops | Alert topic, API alarms, Bedrock and account budgets |

## Problems found and fixed along the way

Each fix has a regression test and a dated note in `tasks.md`.

- **Security.**
  - A test-only login bypass was reachable on the live API. It was closed
    the same day, and a log audit found no outside use.
  - Session cookies needed `SameSite=None` for the cross-site front end.
- **Deployment.**
  - The API container was missing its migrations.
  - The lockfile pinned a Windows-only package unconditionally.
  - The App Runner proxy rejects WebSockets, so chat moved to SSE.
  - Clean URLs needed a CloudFront rewrite.
- **Data correctness.**
  - 2023 team scores with places but no times crashed the API.
  - A team rank was computed after filtering instead of before.
  - The production writer started from an empty database, which blocked all
    ingestion. It now loads the published data at startup.
- **Intake, found by rehearsing the real 2026 Meet 1.**
  - Unmatched school names were silently created as new schools: "BSM" and
    "St. Francis of Assisi (Triangle)" split 44 results off their real teams.
    They now go to review.
  - Review questions were duplicated on every retry.
  - Returning athletes got no roster entry for the new season.
  - Overlapping admin requests broke a decision halfway on the shared
    database connection. Requests now take turns, and decisions are safe to
    retry.
- **Agent quality.**
  - The SQL tool lacked the schema.
  - "Team wins" were confused with individual wins.
  - Python was called without its data.
  - Statistics treated repeat races as independent. A "significant" p =
    0.031 became the correct p = 0.137.

## Owner data correction

- The school recorded since 2023 as "St John the Evangelist" is
  **St John the Beloved** (McLean, VA; Diocese of Arlington). The branch adds
  an audited rename.
- Production gets the correction through the UI after deploying (runbook
  step 9).
- The frozen baseline file is not modified.

## Deployment status

| Part | Status |
|---|---|
| Storage and Web stacks, API, Cognito login, and the first version of the site | Live |
| Everything from Task 19: new site, agent, intake UI, access control, alarms | **Built and tested, not yet deployed** |
| Heroku | Untouched (Task 18.3) |

The exact WSL commands are in `docs/task-19-deploy-runbook.md`.

## Open items

- **Unverified until deployed:**
  - App Runner passing the chat stream through without buffering.
  - The agent runtime's permissions end to end.
- **Needs you in a browser:** the accessibility review (Task 19.5).
- **Data quality:**
  - 2025 Meet 1 JV Girls times run about 50% slower than Meet 3, which
    suggests a wrong distance in the source.
  - 2023 has an "Unknown" school and a "St. Rita Parish Alexandria"
    duplicate.
- **Model access:** newer Claude models (Sonnet 5, Opus 5.x) need access
  enabled in the Bedrock console before they can be evaluated.
- **Not in this commit:** two local backup CSVs
  (`data/merged/season_results_before_mojibakefix.csv`,
  `season_results_pre_fixed.csv`). They are copies of youth results, left
  on disk untracked.
- **Also excluded, and now ignored:**
  `feasibility/agentcore/xcfeasibility/verify_deployment.log`. The pre-commit
  gitleaks scan found an AWS temporary access key ID (`ASIA…`, from the
  2026-09-21 feasibility session) inside its trace metadata. No secret key
  or session token was present, and the session has long expired. The file
  stays on disk only.
