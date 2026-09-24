# Operations and production-acceptance readiness (Task 17.3)

**Prepared:** 2026-09-22. **Status:** staging deployment and production
acceptance (Tasks 17.1, 17.2, 17.4) have **not** happened -- this document
is the evidence package Task 17.4 asks the owner to review, and the
runbook set Task 17.3 asks for, written honestly against what is actually
built rather than the full original scope. It supersedes nothing; it
aggregates.

## 1. What this session covered

Starting from Task 10 (entity resolution) through this document, one
continuous session implemented, in order: deterministic + agent-assisted
entity resolution (Task 10), the Strands agent layer (Task 11), the
end-to-end intake workflow (Task 12), an authenticated FastAPI + WebSocket
API (Task 13), a minimal real Next.js frontend (Task 14), a partial CDK
infrastructure-as-code app (Task 15), and an integrated
security/resilience review (Task 16). Every task's own `tasks.md` entry
carries a dated `**DONE**`/`**PARTIAL**`/`**NOT DONE**` note with specific
file and test citations -- this document does not repeat that detail, only
synthesizes it.

**Explicit owner decision carried through this entire session** (recorded
at Task 11's start): write and test infrastructure/agent/API code fully,
but make **no live AWS call and no deployment**. Every "not done" item
below traces back either to that boundary or to a feature whose underlying
engine (team scoring, Saint Sebastian standings, full frontend) was out of
this session's scope from the start.

## 2. Source-to-canonical reconciliation

The frozen 2023-2025 historical baseline's reconciliation (Task 5) is
unchanged and still authoritative for that data: `docs/
reconciliation-report.json` -- 0 discrepancies, 34 explained observations,
4,203 canonical results against a 4,204-row baseline (the one-row gap is a
documented team-only quarantine, R1.10). Nothing in this session touched
that historical dataset.

This session's own reconciliation evidence is the intake-workflow test
suite itself, not a separate report: `src/xc_platform/ingest/
test_workflow.py` and `src/xc_platform/api/test_admin_ingest.py` prove, end
to end and repeatedly, that a submitted URL's rows reconcile exactly to
inserted-canonical-result counts (`inserted_count` asserted against the
real row count), that quarantined/unchanged rows are counted and never
silently dropped, and that a repeated commit is idempotent (zero
duplicate results). There is no live 2026-season data to reconcile against
yet -- no real RunSignup import has been run against production data this
session; every test uses scripted fixture responses.

## 3. Known gaps (aggregated)

This is the complete list of `**PARTIAL**`/`**NOT DONE**` items recorded
in `tasks.md` for Tasks 10-16, grouped by what would close them:

**Needs live AWS (blocked by this session's no-deploy decision):**
- AgentCore Runtime/Memory deployment and verification (Task 11.5/11.6) --
  code is written and unit-tested against a scripted model; `/ping`,
  real streaming, real session/actor isolation under a `CUSTOM_JWT`
  authorizer, and cold-start behavior are unverified.
- Cognito integration (Task 13.4/14.1) -- `login_with_verified_identity`
  is the real seam; no live user pool was exercised. `/auth/dev-login` is
  an explicitly-marked local-only stand-in.
- CloudFront, API Gateway, Lambda, WebSocket API, SQS (Task 15.2, most of
  15.1) -- only the S3/SQS-FIFO/IAM-role half of storage (Task 15.1) has
  CDK; nothing has been synthesized for the web/API/streaming layer, and
  nothing anywhere has been deployed.
- Performance targets (Task 16.4) and staging smoke/accessibility/recovery
  demonstrations (Task 17.1) -- both need a running deployed system to
  measure or exercise against.

**Needs analytics engines this session did not build (Task 11.1's
documented gap):**
- Team scores, Saint Sebastian standings, improvement candidates,
  head-to-head comparison, what-if scenarios -- no rule engine exists for
  any of these; there is accordingly no API endpoint or frontend page for
  them either (Tasks 13.2, 14.2, 14.3).

**Needs frontend work not attempted this session (Task 14.2-14.6):**
- Full dashboard rebuild, advanced athlete/school views, the Ask-the-Data
  chat UI, all administration UIs, and WCAG 2.1 AA validation. The backend
  every one of these would call is real and tested; no page calls it yet
  except the one dashboard search built in Task 14.1.

**Smaller, independently closeable gaps:**
- No `approve` path for resolution decisions via the API (only `reject`/
  keep-separate) -- approving a match needs a losing-entity-id parameter
  the endpoint doesn't accept yet.
- No `POST /ingest-runs/{id}/cancel`, no publication history/restore
  endpoints, no pagination on any list endpoint, no generated (vs.
  hand-written) OpenAPI client.
- No true token-by-token WebSocket streaming (one complete `markdown_delta`
  per turn, not incremental deltas) and no chart/table/tool_summary event
  types (no chart-generating tool is wired into the agent yet, though the
  chart contract and validator themselves are built and tested, Task
  11.2).
- Decompression-bomb payloads and Bedrock/AgentCore/SQS-specific failure
  drills are untested (Task 16.2/16.3).

## 4. Architecture decisions recorded this session

- **Cookie session (BFF), never bare bearer-JWT** (Task 13.4). Directly
  driven by the Task 3.7 feasibility finding
  (`docs/protected-api-evidence.md`): a stateless JWT authorizer does not
  see Cognito revocation. `security/session.py`'s server-side session
  store makes revocation take effect on the next request, proven by a
  dedicated test.
- **CSRF via double-submit cookie**, enforced on every mutating request
  except safe methods (`api/deps.py`).
- **WebSocket connection tickets as a subprotocol, never a query
  parameter** (Task 13.5), promoted from the Task 3.7 feasibility gate's
  validated logic (`security/connection_ticket.py`), with the single-use
  replay store explicitly documented as needing a shared backend
  (DynamoDB/ElastiCache) in production -- an in-process set is
  local/test-only.
- **Markdown sanitization added this session** (`security/
  markdown_sanitizer.py`) -- a real gap this review found: the system
  prompt told the model to respond in "sanitized Markdown," but nothing
  enforced it server-side before this pass.
- **Deterministic-first entity resolution, agent as review-enrichment
  only** (Tasks 10, 11.4, 12.3): the resolution agent never auto-merges
  anything; every proposal is review-only until an owner explicitly
  enables an evaluated auto-match threshold, which nothing in this
  codebase does.
- **CDK for storage/queues only, not the rest of infrastructure** (Task
  15) -- AgentCore infrastructure specifically is left to the `agentcore`
  CLI's own generated CDK app rather than a second, parallel hand-rolled
  definition.
- **A minimal real Next.js shell over a full frontend build-out** (Task
  14) -- an explicit scope cut favoring keeping the Python backend real
  and tested over a large, unverifiable (no GUI browser in this
  environment) frontend effort.

## 5. Runbooks

### Run the test suite
```bash
.venv/Scripts/python.exe -m pytest src/xc_platform -q
.venv/Scripts/python.exe -m ruff check src/xc_platform
.venv/Scripts/python.exe -m mypy src/xc_platform
```

### Run the app locally (manual/demo use only -- see `src/xc_platform/cli/
run_local_api.py`'s own docstring: not a deployment path)
```bash
# Terminal 1, from the repo root:
PYTHONPATH=src .venv/Scripts/python.exe -m xc_platform.cli.run_local_api
# Terminal 2:
cd web && pnpm dev
```

### Synthesize infrastructure (never deploys)
```bash
cd infrastructure/cdk
.venv/Scripts/python -m pytest test_storage_stack.py -q
PATH="$PWD/.venv/Scripts:$PATH" npx --yes aws-cdk@2.1032.0 synth
```

### Dependency review and lockfile updates
Follow `docs/dependency-review.md` exactly; every dependency added this
session (`strands-agents`, `bedrock-agentcore`, `fastapi`, `uvicorn`, the
frontend's `next`/`react`/`tailwindcss`) followed that process, including
two real `pnpm audit` catches (a deprecated/vulnerable `next@15.5.4` pin,
then a critical unauthenticated-RCE advisory requiring `next>=16.3.3`)
resolved before anything shipped.

### Publication and restore
`xc_platform.db.publication.writer.SnapshotPublisher`/`reader.
SnapshotReader` (Task 6, unchanged) implement publish and the "last valid
snapshot" read-through; there is no `restore`-to-a-prior-generation CLI or
API endpoint yet (recorded gap, Task 13.3).

### Incident response (what exists today)
- A failed commit transaction rolls back and marks the ingest run
  `failed` with an explanatory `error_summary` (`ingest/workflow.py`,
  tested).
- A failed publication *after* a successful local commit is distinguished
  from a transaction failure and reported as such; the local rows are
  real and permanent, recovering them into a new snapshot needs a
  follow-up publish (no automatic retry exists for this specific case --
  documented in `commit_scope`'s own comments).
- A revoked session takes effect on the next request (Task 13.4).
- There is no on-call/paging setup, no CloudWatch alarm configuration, and
  no incident runbook beyond what is described here -- all of that needs
  the deployed infrastructure this session did not build.

### Memory deletion
`security/session.py`'s `revoke_all_for_actor` is real and tested (a
global sign-out revokes every session an actor holds). AgentCore Memory's
own per-user reset/delete workflow (Requirement 11.7) is **not
implemented** -- Task 11.6's recorded gap; no AgentCore Memory resource
exists to delete from yet.

### Support
No support contact or escalation path is documented -- out of scope for a
single-operator project at this stage; add one before onboarding real
users.

## 6. Exact versions in play

- Python 3.12 (`.venv`), pinned via `requirements.lock`.
- Key new dependencies this session: `strands-agents==1.56.0`,
  `bedrock-agentcore==1.23.1`, `fastapi==0.141.1`, `uvicorn==0.53.0`.
- Frontend: Node 20.19.1, pnpm 10.26.1 (both pre-pinned), `next==16.3.6`
  (bumped twice this session for real vulnerabilities before landing on a
  clean `pnpm audit`).
- CDK subproject: `aws-cdk-lib==2.216.0`, `constructs==10.4.2`, its own
  isolated venv.
- No AWS account, Region, or quota has been provisioned or approved yet --
  there is nothing deployed to quote a version or quota for.

## 7. What Task 17.4 (owner acceptance) needs to happen next

This document, plus each task's own `tasks.md` evidence trail, is the
package for that decision. What it cannot include, because none of it has
happened: a staging deployment (17.1), a staging-environment URL intake
demonstration (17.2), or measured production-like performance (16.4). If
the owner's acceptance decision requires those, the next session's first
job is Task 15.2/15.3's remaining infrastructure, a real `cdk deploy`, and
then 17.1/17.2 against that live environment -- all owner-authorized
actions this session deliberately did not take unilaterally.
