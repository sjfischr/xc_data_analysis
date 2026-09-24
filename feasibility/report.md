# XC Data Platform — Feasibility Report

**Status:** Final — **approved by the owner 2026-09-21** (Task 3.8 complete; see §9)
**Probes executed:** September 20–21, 2026
**Account:** AWS `918221680168`, region `us-east-1`, identity
`arn:aws:iam::918221680168:user/openmhz-deployer`
**Spec:** [requirements.md](../.kiro/specs/xc-data-platform/requirements.md) ·
[design.md](../.kiro/specs/xc-data-platform/design.md) ·
[tasks.md](../.kiro/specs/xc-data-platform/tasks.md)

---

> **Amended 2026-09-20, after these probes ran, by owner decision:**
> **(1) Cost is no longer a requirement or a gate anywhere in this spec** — the
> USD 20 ceiling, forecasts, budget alerts, cost quotas, and gate F9 are
> removed. The cost observations below are retained as operational telemetry
> and as context, not as gating criteria. **(2) The 2023, 2024, and 2025 seasons
> are frozen** — migrated exactly as the baseline holds them, never corrected or
> backfilled from the live source; live intake targets **2026 only**. The
> source-drift and Meet 2 findings below therefore become documented
> observations rather than reconciliation work.

## 1. Decision summary

**GO — approved by the owner on 2026-09-21. Task 4 is authorized to begin.**

Seven of the ten architecture gates passed clean. One passed with a documented
caveat. Two needed owner action rather than more probing — the AgentCore
deployment needed an approval this session could not give itself, and the
protected-delivery prototype needed the same for its live proof — and both
were resolved live on 2026-09-21.

Nothing discovered contradicts the architecture. The publication protocol, the
RunSignup adapter path, the agent tool constraints, and the auth model all
behaved as the design predicted. Several findings *changed* the design
(inference profiles, mirror domains, and — by owner decision — dropping
non-RunSignup extraction fallback from release 1 entirely) and several
strengthened it, including a live AgentCore deployment that caught a real
actor-isolation gap, retested it twice against different explanations, and
confirmed design §12.4's identity-derivation rule (a verified
`CUSTOM_JWT`/Cognito token, not any client-supplied value) is the *only*
mechanism that can work under this SDK, not merely the preferred one (§3.16).

### Conditions attached to the go decision — see §9 for the full record

1. **Non-RunSignup extraction fallback is dropped from release 1 entirely**
   (not shipped experimental) — the spec has been amended throughout to match.
2. **Actor isolation remains unverifiable until Task 15.2** provisions a
   `CUSTOM_JWT` authorizer and Cognito user pool. Do not enable multi-user
   agent access before that exists and is retested.
3. **Task 13.4's auth must be the BFF-cookie session design §6.2 already
   specifies, not a bearer-JWT shortcut** — live evidence shows the shortcut
   cannot deliver R13.7's revocation criterion.
4. **Model selection and resolution policy are not yet finalized** — the owner
   wants further discussion before confirming Haiku 4.5 / review-only.
   Task 4's schema work does not depend on this; revisit before Task 11.

---

## 2. Gate outcomes

| Gate | Subject | Verdict | Basis |
|---|---|---|---|
| **F1** | RunSignup coverage | **PASS** | 152 requests, all HTTP 200; every event, result set, and row enumerated for 2023–2026 |
| **F2** | Historical Meet 2 | **PASS** | Conclusively resolved — see §3.1 |
| **F3** | URL scope normalization | **PASS** | Supplied URL resolved to a real result set; a better URL form was found |
| **F4** | Tavily discovery | **PASS (caveat)** | Search is useful; Map is not. Passes on REST enumeration, not on Map |
| **F5** | Tavily fallback extraction | **UNVERIFIED** | No genuine non-RunSignup source produced usable rows |
| **F6** | SQLite/S3 safety | **PASS** | 14/14 checks against real S3, including concurrent writers and injected failures |
| **F7** | Agent quality | **PASS** | 0 false merges on both models; 7/7 analytics after a harness fix |
| **F8** | AgentCore runtime | **PASS except actor isolation** | Deployed and retested live 2026-09-21; 9/10 checks pass; actor isolation structurally blocked until a `CUSTOM_JWT` authorizer exists |
| ~~**F9**~~ | ~~Cost~~ | **REMOVED** | Withdrawn 2026-09-20 by owner decision; cost is no longer a gate |
| **F10** | Protected delivery | **PASS (with a design-confirming finding)** | Live protected API deployed and torn down 2026-09-21; anonymous/role enforcement all correct; a real revocation gap in the bearer-JWT alternative confirms why the design's BFF-cookie approach is required, not optional |

---

## 3. What the probes established

### 3.1 The Meet 2 data gap is real for 2023 — and already closed for 2024

The repository's `MEET2_DATA_GAP_ANALYSIS.md` (October 2025) states that both
2023 and 2024 Meet 2 lack individual results. **That is now out of date.**

Querying the source directly:

- **2023 Meet 2:** eight result sets are published, each containing **zero
  rows**. The gap is real, it is at the source, and it is not a parsing defect.
  No amount of re-fetching will produce these rows.
- **2024 Meet 2:** **495 athlete-level rows**, matching the frozen baseline's
  495 exactly.

The one remaining 2023 Meet 2 record in the baseline (Frosh, 1 record) has no
counterpart at the source — the "stray record" the old report flagged.

**Consequence:** the documented gap narrows to 2023 Meet 2 only. It stays a
gap — reported as missing (R1.7), never synthesized, and under the 2026-09-20
amendment never backfilled either (R1.10).

### 3.2 Live source data has diverged from the frozen baseline

Comparing live counts to the baseline across 72 season/meet/division/gender
cells: **58 match exactly, 14 differ.** Excluding the entirely new 2026 season,
eight cells differ:

| Season-Meet | Baseline | Live | Delta |
|---|---|---|---|
| 2023 M2 Frosh M | 1 | 0 | −1 |
| 2023 M3 Frosh F | 100 | 110 | +10 |
| 2025 M1 Frosh F | 106 | 105 | −1 |
| 2025 M2 Frosh F | 100 | 110 | +10 |
| 2025 M2 Frosh M | 96 | 98 | +2 |
| 2025 M2 JV M | 94 | 96 | +2 |
| 2025 M3 JV F | 100 | 106 | +6 |
| 2025 M3 JV M | 100 | 103 | +3 |

The source now publishes **more** rows than the saved pages captured — results
added or corrected after the pages were saved. The baseline was not modified
(R1.1).

**Under the 2026-09-20 amendment these differences are recorded here and left
alone.** 2023–2025 are frozen: the baseline is the parity target, and the drift
above is a documented observation, not reconciliation work (R1.8–R1.9).

**A whole new season also appeared: 2026 Meet 1, 577 rows** — and under the
amendment this is exactly where live intake is now aimed.

### 3.3 The frozen baseline encodes gender inconsistently

The baseline uses `F`/`M` for some season-meets and `Boys`/`Girls` for others:

| Season-Meet | Labels used |
|---|---|
| 2023 M1 | `F`, `M` |
| 2023 M2 | `Boys` |
| 2023 M3 | `Boys`, `Girls` |
| 2024 M1–M3 | `Boys`, `F`, `Girls`, `M` (mixed within the same meet) |
| 2025 M1–M3 | `Boys`, `Girls` |

Before canonicalizing, 97 of 113 comparison cells appeared to differ; afterwards
only 14 do. **Task 5.1 must normalize this explicitly while preserving the
original value.** Any parity comparison that skips this step will report
nonsense.

### 3.4 A malformed row exists in the baseline

Exactly one row has an **empty athlete name** (1,441 distinct values vs 1,440
non-empty). A quarantine candidate for Task 5.1, with a reason code.

### 3.5 Custom field IDs are per-result-set

The same header label appears under many numeric IDs across the series — "Team
Name" under 45 distinct `custom-field-NNNNNN` ids, "Year" under 42, "Team Score"
under 41. **Mapping by header label (design §9.3) is mandatory**, not a
preference. Full detail in [runsignup-adapter-notes.md](../docs/runsignup-adapter-notes.md).

### 3.6 RunSignup reports errors as HTTP 200

Invalid race IDs, unknown events, and missing parameters all return **HTTP 200**
with an `error` envelope in the body. An adapter that keys its retry policy on
status codes alone would retry permanent errors forever. Every response must be
inspected for an `error` key.

### 3.7 The URL fragment should be rewritten as a query parameter

The supplied URL carries scope in a fragment (`#resultSetId-691534;perpage:100`)
which browsers never transmit. Search discovered a server-side form:
`?resultSetId=NNNNNN`. A controlled ablation on the same page:

| URL form | Extract depth | Content | Name recall |
|---|---|---|---|
| `?resultSetId=493637` | basic | 19,320 chars | **20/20** |
| bare `/Results/154050` | advanced | 1,928 chars | 0/20 |

The **query parameter**, not extraction depth, is what makes the page readable.
URL normalization should rewrite fragment scope into query scope: it makes
submitted URLs addressable, shareable, and extractable, at half the credit cost.

Incidentally, the supplied URL's result set `691534` resolves to **2026 Varsity
Girls** — the new season, not a historical one.

### 3.8 White-label mirror domains serve identical race IDs

`trisignup.com` and `adventuresignup.com` serve **the same numeric race IDs** as
`runsignup.com`. If the source router does not treat them as RunSignup-family
hosts, the same race will be ingested twice under different provenance,
manufacturing duplicate meets and athletes for entity resolution to unpick.
Encoded as `RUNSIGNUP_FAMILY_HOSTS` with a contract test.

### 3.9 The publication protocol works exactly as designed

14/14 checks against real S3 — immutable snapshots, single-writer lease,
manifest compare-and-swap, three injected failures, corruption detection, and
append-only restore. Measured: 0.83 s publish, 0.66 s download, **0.016 s**
verify, 0.61 s restore on a 1.3 MiB database.

**The most important detail:** a snapshot with 64 zeroed bytes **passed**
`PRAGMA integrity_check` and was caught only by the SHA-256. Verification must
fail closed on digest mismatch regardless of what SQLite reports. Full detail in
[s3-publication-evidence.md](../docs/s3-publication-evidence.md).

### 3.10 Agent tool constraints hold under attack

Twelve attack patterns — stacked statements, `PRAGMA`, `ATTACH`, `CREATE TABLE
AS SELECT`, comment-smuggled writes, `sqlite_master` access — were all refused.
`sqlite_master` was blocked by the **SQLite authorizer**, below the text check,
confirming the defense-in-depth layering. The read-only connection independently
rejects writes.

### 3.11 Bedrock requires inference profile IDs

Bare model IDs fail with "on-demand throughput isn't supported". Model routing
must use `us.`-prefixed inference profiles or every agent call fails.

### 3.12 Model evaluation: zero false merges, conservative routing

Both Haiku 4.5 and Sonnet 4.5 kept **all four confirmed-distinct sibling pairs
separate** and scored **7/7** on analytics fixtures. Both scored 2/4 on routing
ambiguous pairs to review; Sonnet was more conservative overall (6/10 vs 8/10 on
the curated set) while erring in the safe direction.

**Recommendation: Haiku 4.5 as default; escalation configured but disabled;
resolution agent review-only.** Detail in
[model-evaluation-evidence.md](../docs/model-evaluation-evidence.md).

### 3.13 Auth carries role claims without PII

`cognito:groups` appears in both ID and access tokens, so the API can decide
roles from the presented token. **No email appears in the token**, so the
pseudonymous actor-id HMAC can be derived from `sub` with no PII in the memory
namespace. Self sign-up is refused outright. Detail in
[auth-delivery-evidence.md](../docs/auth-delivery-evidence.md).

### 3.14 Tavily metering cannot rely on `/usage`

The `/usage` counters did not move after successful operations, and the endpoint
returns **HTTP 429** under modest polling. Credits must be metered from the
platform's own request log against the documented rate card, with `/usage` used
only as an occasional reconciliation check. Detail in
[tavily-routing-notes.md](../docs/tavily-routing-notes.md).

### 3.15 The Python AgentCore starter toolkit is deprecated

`bedrock-agentcore-starter-toolkit` prints a deprecation notice directing users
to the npm `@aws/agentcore` CLI, and its `deploy --help` crashes with a
traceback. The Task 2 skills were written against the Python toolkit and need
updating. The npm CLI works and deploys via CDK.

### 3.16 A live AgentCore deployment caught a real actor-isolation gap -- and showed it can't be closed without Cognito

*(2026-09-21.)* The AgentCore CLI's `RequestContext` has no `user_id` field;
`agentcore invoke --user-id <x>` never reaches the entrypoint. Code that reads
`getattr(context, 'user_id', 'default-user')` -- the scaffold's own default --
silently collapsed every caller onto one actor, reproduced live: a second,
differently-`--user-id`'d call read the first caller's conversation.
AgentCore Memory's own event filtering by `actor_id` was verified correct by
reading the SDK; the bug was entirely in identity derivation, not the memory
service.

The fix (derive identity from the `Authorization` header the SDK forwards) was
deployed and retested with two genuinely different, distinctly-`sub`'d JWTs --
and the leak persisted, for a different reason: `--bearer-token` is rejected
outright (HTTP 403) by this runtime's default `AWS_IAM` authorizer, and a
custom `-H "Authorization: ..."` header most likely collides with the
SigV4 signature that authorizer requires, so no identity reaches the
entrypoint either way -- both callers fall back to the same constant.

**Actor isolation is not achievable, or even testable, without
`authorizerType: CUSTOM_JWT` against a real Cognito user pool.** This confirms
design §12.4's HMAC-of-Cognito-`sub` approach is not a preference but the only
mechanism this SDK supports, and it moves the verification of R11.5-R11.9
(actor isolation) from Task 3.5 to Task 11.5/15.2, where Cognito actually
exists. Full detail in
[agentcore-deployment-evidence.md](../docs/agentcore-deployment-evidence.md).


### 3.17 A live protected API confirmed why bearer-JWT-only auth is not acceptable

*(2026-09-21.)* A disposable Cognito pool + API Gateway JWT authorizer + Lambda
prototype confirmed anonymous rejection and role enforcement work exactly as
expected (8/9 direct checks). One check went further: after revoking a user's
session with `admin-user-global-sign-out`, the same still-unexpired token was
rejected by Cognito directly but still accepted by the stateless JWT
authorizer -- a revoked user's access persisted until natural token expiry.
This is a property of bearer-JWT-only authorization, not a bug, and it is
exactly why design §6.2 specifies a backend-for-frontend cookie session with a
server-side-checked record instead. Task 13.4 now carries an explicit caution
against the bearer-JWT shortcut. Full detail in
[protected-api-evidence.md](../docs/protected-api-evidence.md).


---

## 4. Gate detail

Extended narrative for the gates whose story doesn't fit a table row. F8 is
the one genuinely still open; F10 is included here only because of length --
it is resolved (§2, §3.17).

### F8 — AgentCore runtime: deployed, verified, actor isolation retested twice

The owner ran `agentcore deploy`. All 7 resources reached `CREATE_COMPLETE` in
4m 3s: `AWS::BedrockAgentCore::Runtime` (state `READY`),
`AWS::BedrockAgentCore::Memory`, 2 IAM roles, 1 policy, plus CDK metadata and
the stack itself. `feasibility/agentcore/xcfeasibility/verify_deployment.sh`
then exercised the live runtime: **9 of 10 checks passed.**

Confirmed live: correct tool-grounded invocation (4,204 results,
`publication_id` reported), streaming, short-term context within a session,
session isolation across sessions, a live `DROP TABLE` attempt refused by the
deployed tool (not just the local prototype), a prompt-injection attempt
correctly deflected, rich structured OpenTelemetry tracing, and no raw
chain-of-thought in logs.

**Actor isolation failed, was root-caused, fixed, redeployed, and retested —
and failed again for a different, more fundamental reason.**

*First failure.* Reusing a session id with a different `--user-id` leaked the
first caller's conversation to the second. Reading the installed SDK
(`bedrock_agentcore==1.23.1`) confirmed the cause precisely:
`RequestContext` has no `user_id` field at all, so the scaffold's
`getattr(context, 'user_id', 'default-user')` silently returned the same
constant for every caller. **AgentCore Memory itself was not at fault** —
`AgentCoreMemorySessionManager.list_messages` correctly filters every read by
both `actor_id` and `session_id`; the leak was entirely in how the scaffold
derived `actor_id`.

*Fix, redeploy, and retest.* The entrypoint was changed to derive identity from
the `Authorization` header the SDK forwards into `request_headers`. The owner
redeployed (`UPDATE_COMPLETE`, 1m 7s, code-only). Retesting with two unsigned
JWTs carrying distinct `sub` claims: `agentcore invoke --bearer-token` was
rejected outright at the gateway (HTTP 403 — this runtime uses the default
`AWS_IAM` authorizer, which does not accept bearer tokens at all), and
`-H "Authorization: Bearer <token>"` layered on top of the normal SigV4-signed
request was accepted by the gateway but **still leaked**: agent A was asked to
remember an arbitrary number (71553, chosen to have no relationship to the
dataset, ruling out a tool-call coincidence); agent B, same session, a
different token, was asked for it, and answered with the number, explicitly
citing "conversation history."

**Conclusion, and why this stops here rather than at a fourth attempt:** under
`AWS_IAM` authorization, the wire-level `Authorization` header is reserved for
the request's own SigV4 signature. A custom header of the same name most
likely never reaches the entrypoint intact, so identity derivation falls
through to its `"unauthenticated"` default for every caller — a different
failure mode than the original bug, but the same net effect. **Actor isolation
cannot be achieved, or even properly tested, under this runtime's current
authorizer.** It requires `authorizerType: CUSTOM_JWT` against a real Cognito
user pool — Task 15.2/11.5 scope, not a fix available inside this feasibility
stack's current configuration. The code fix itself is a strict improvement
(it no longer trusts any client-declared value) and should carry forward
unchanged into Task 11.5; it simply cannot be validated further here.

Also confirmed: this CLI build (`@aws/agentcore`) has no per-actor memory
delete/reset command. Task 13.5's `DELETE /chat/memory` must call the
AgentCore Memory data-plane API directly.

Full detail:
[agentcore-deployment-evidence.md](../docs/agentcore-deployment-evidence.md).

**Status:** proven for everything except actor isolation, which is now known
to require Cognito/`CUSTOM_JWT` infrastructure before it can be verified at
all. **Do not enable multi-user agent access before that exists and is
retested.** The stack was torn down by owner decision on 2026-09-21
(`aws cloudformation delete-stack`; confirmed absent) — Task 15.2/11.5 will
redeploy it once a `CUSTOM_JWT` authorizer exists.

*(F9 -- cost -- was withdrawn as a gate on 2026-09-20; see §5 for the two
operational facts worth retaining from that work. It is not listed here as
"unresolved" because it no longer exists as a requirement to resolve.)*

### F10 — Protected delivery: resolved live, 2026-09-21

Auth and connection-ticket logic proven earlier (14/14 tests, still passing)
was extended with the piece that was missing: an actual protected HTTP entry
point, deployed and load-tested with real tokens against real infrastructure,
then fully torn down.

**Built:** a disposable Cognito pool (invite-only, `admin`/`viewer` groups), an
API Gateway HTTP API with a native Cognito JWT authorizer, and one least-
privilege Lambda exposing a public route, an authenticated-any-role route, and
an admin-only route enforced server-side.

**Result: 8 of 9 direct checks passed exactly as expected** — anonymous callers
get 401 on every protected route and nothing on the public one; viewer and
admin tokens each get exactly the access their role allows; a garbage token is
rejected. One check was informational (the authorizer accepts a token without
the literal `Bearer ` prefix — not a bypass, since the token itself is still
fully verified either way).

**One significant, live-confirmed finding.** After
`aws cognito-idp admin-user-global-sign-out`, the same still-unexpired access
token was rejected by Cognito directly (`GetUser` → *"Access Token has been
revoked"*) but **still accepted** by API Gateway's JWT authorizer, returning a
full `200` response. A stateless JWT authorizer checks only the token's own
signature, issuer, audience, and expiry — it never calls back to the issuer to
ask about revocation. A revoked user's token therefore stays fully valid
against this kind of API until it naturally expires (up to the full 1-hour TTL
configured here).

This is not a flaw in the prototype; it is a property of bearer-JWT-only
authorization that the design already anticipated and routed around. Design
§6.2 specifies a backend-for-frontend cookie session, checked against a
server-side session record on every request, precisely because that is what
makes revocation take effect on the *next* request rather than at token
expiry. This probe existed to test whether the simpler bearer-JWT alternative
could substitute for that -- and it demonstrably cannot, for R13.7 specifically.
Task 13.4 has been amended with an explicit caution against that shortcut.

Anonymous access to race data and administrative operations is confirmed
blocked (checks 1-3, 5). The same authorizer-plus-server-side-check mechanism
applies uniformly to any route behind the API Gateway, so this generalizes to
a future agent-chat route without needing a separate live test of one.

Lambda@Edge comparison remains analysis-only (see
[auth-delivery-evidence.md](../docs/auth-delivery-evidence.md));
nothing measured here changes that conclusion, since the revocation finding is
about the authorization mechanism, not the edge-routing choice.

Full detail:
[protected-api-evidence.md](../docs/protected-api-evidence.md).

**Status:** resolved and closed. Kept in this section (rather than moved
entirely into §3) because of its length; the summary lives in §2's gate table
and §3.17. All resources created for this probe were torn down and confirmed
absent.

## 5. Cost position — withdrawn

*(Removed 2026-09-20 by owner decision. Cost is no longer a requirement, an
acceptance criterion, or a gate. The measurements that were taken are retained
as telemetry in §4 and in
[model-evaluation-evidence.md](../docs/model-evaluation-evidence.md).)*

Two operational facts remain worth knowing, as facts rather than constraints:

- The Tavily account had ~197 of 1,000 monthly credits remaining, so a
  runaway import could exhaust the plan allowance. This is why per-run page and
  request limits stay in the spec as **runaway protection**.
- The account already runs an unrelated AgentCore runtime (`rivelMarketIntel`),
  so any account-level usage view mixes in spend that is not this platform's.

## 6. Recommendations to carry into implementation

### 6.1 Final model IDs

| Role | Model | Basis |
|---|---|---|
| Default | `us.anthropic.claude-haiku-4-5-20251001-v1:0` | Equal analytics accuracy (7/7) and confirmed-distinct safety to Sonnet; better curated-resolution agreement (8/10 vs 6/10); ~2x lower latency |
| Escalation (configured, disabled) | `us.anthropic.claude-sonnet-4-5-20250929-v1:0` | No measured dimension favored it; kept wired for a future need-check, not enabled now |

**Both IDs must be `us.`-prefixed inference profiles.** Bare model IDs are
rejected by Bedrock outright ("on-demand throughput isn't supported").

### 6.2 Runtime packaging

**Pure-Python CodeZip**, as design §3.5 preferred. Confirmed live (Task 3.5):
the Strands agent, its tools, and `bedrock-agentcore`'s memory integration
deployed and ran with no native/compiled dependency and no container build.
No fallback build method was needed.

### 6.3 Tavily limits

| Limit | Value | Basis |
|---|---|---|
| Max pages per run | 25 | Map returned ≤3 useful pages in testing; generous headroom |
| Max crawl depth | 2 | Depth 2 added one URL over depth 1 — deeper is waste |
| Max wall-clock per run | 120 s | Slowest observed operation was 3.4 s |
| Max response bytes | 8 MiB | Largest observed response was 19 KB |
| Extraction depth | basic | Identical name recall to advanced, half the credit cost |
| Max credits per import | 10 | ~5% of the account's remaining monthly allowance at probe time |

Meter from the platform's own request log against the documented rate card;
`/usage` did not reflect completed operations and returned HTTP 429 under
modest polling.

### 6.4 Auth delivery

**Backend-for-frontend cookie session** (design §6.2's existing choice), not a
bearer-JWT-only pattern. Confirmed live (Task 3.7): a revoked user's bearer
token stayed fully valid against a stateless JWT authorizer until natural
expiry (up to the configured 1-hour TTL); only a server-side session check
makes revocation immediate, which R13.7 requires.

### 6.5 Recovery objectives

Measured against a 1.3 MiB representative snapshot (Task 3.4); re-measure at
production data volume before treating these as committed SLOs:

| Operation | Measured |
|---|---|
| Snapshot download + full verification (cold reader) | ~1.3 s (0.66 s download + 0.016 s verify, plus manifest fetch) |
| Restore to any prior generation | ~0.6 s |
| Publish (upload + manifest swap) | ~0.83 s |

**Recommended RPO:** zero for committed publications — the protocol makes a
partial write unobservable to readers by construction (upload-then-CAS
ordering), so there is no window in which a reader can see a corrupt or
half-applied generation. **Recommended RTO:** under 5 seconds for a restore to
any previously verified generation, with wide margin over the ~0.6 s measured.

### 6.6 Runaway caps (not cost controls — see §5)

| Cap | Value | Basis |
|---|---|---|
| Agent tool row limit | 200 rows | `MAX_ROWS` in the feasibility prototype's query tool |
| Agent tool query time limit | 5 s | `MAX_QUERY_SECONDS`, enforced via SQLite progress handler |
| Tavily per-run limits | see §6.3 | measured against observed operation cost |
| RunSignup concurrent requests | 2 | documented API guidance; the probe stayed within it (152 sequential requests, zero 429s) |

### 6.7 Source-specific fallback

**Non-RunSignup fallback ships experimental**, gated behind explicit manual
review. No genuine non-RunSignup source produced usable rows during Task 3.3
(the one tested, a MileSplit team page, returned 296 characters with no
results table). Do not treat Tavily Extract as production-ready for any source
other than RunSignup-family hosts until a specific source is approved and
measured.

### 6.8 Other findings to carry forward

| # | Recommendation | Affects |
|---|---|---|
| 1 | Keep the resolution agent **review-only**; no auto-match threshold | Task 10.3, 11.4 |
| 2 | Seed curated `apply` corrections as **approved aliases** so they need no model call | Task 5.2 |
| 3 | Give agents **rich schema description**; treat it as a correctness requirement | Task 11.1 |
| 4 | Treat `trisignup.com` / `adventuresignup.com` as **RunSignup-family hosts** | Task 8.1 |
| 5 | **Rewrite fragment scope into `?resultSetId=`** during URL normalization | Task 8.1 |
| 6 | Inspect **every** RunSignup response for an `error` key; HTTP 200 ≠ success | Task 8.3 |
| 7 | Map custom fields **by header label**, never numeric ID | Task 8.2 |
| 8 | Detect fetch completion by **terminal short page + unique ID accounting** | Task 8.3 |
| 9 | Verification must **fail closed on SHA mismatch**; `integrity_check` is insufficient | Task 6.2 |
| 10 | Normalize the baseline's **mixed gender encodings**, preserving originals | Task 5.1 |
| 11 | Quarantine the **one empty-athlete-name row** with a reason code | Task 5.1 |
| 12 | Preserve **2023 Meet 2** as a source gap; never synthesize or backfill | Task 5.1 |
| 13 | **Freeze 2023–2025**; parity target is the baseline, not the live source | Task 5.1, 5.4 |
| 14 | **Refuse imports resolving to a frozen season**; live intake is 2026-only | Task 8.1, 12.1 |
| 15 | The supplied spec URL resolves to **2026** Varsity Girls — usable as the 2026 intake demo | Task 12.5, 17.2 |
| 16 | Derive `actor_id` **only** from the verified `Authorization` header under a `CUSTOM_JWT` authorizer; `context.user_id` does not exist, and `AWS_IAM` cannot carry per-user identity at all | Task 11.5, 11.6 |
| 17 | **Do not enable multi-user agent access until a `CUSTOM_JWT` authorizer + Cognito user pool exist and actor isolation is retested** | Task 11.5, 15.2 |
| 18 | Build `DELETE /chat/memory` against the AgentCore Memory data-plane API directly; no CLI command exists for it | Task 13.5 |
| 19 | Connection-ticket replay protection needs a **shared TTL store** | Task 13.5 |
| 20 | Pin **boto3** for production S3 work (the probe used the AWS CLI) | Task 6 |
| 21 | Update the AgentCore skills for the **npm CLI**; the Python toolkit is deprecated | Task 2 |

---

## 7. Versions and environment

| Component | Version |
|---|---|
| AWS CLI | 2.36.49 (2.13.34 also present on PATH — see note) |
| `strands-agents` | 1.56.0 |
| `bedrock-agentcore` | 1.23.1 |
| `bedrock-agentcore-starter-toolkit` | 0.3.13 (deprecated) |
| `@aws/agentcore` (npm) | current at 2026-09-20 |
| `boto3` | 1.43.98 (feasibility venv only, not in `requirements.lock`) |
| Python | 3.12.10 (project), 3.14 (AgentCore runtime target) |
| Node | 20.19.1 |
| Tavily rate card | fetched 2026-09-20 |

**Environment notes.** Norton intercepts TLS on this machine; the AWS CLI, the
Python SDKs, and `uv` all need `AWS_CA_BUNDLE` / `SSL_CERT_FILE` /
`UV_NATIVE_TLS=1` pointed at Norton's root, or they fail with
`CERTIFICATE_VERIFY_FAILED`. Two AWS CLI installations exist; the older 2.13.34
resolves first on `PATH` and lacks AgentCore commands entirely.

No production Python dependencies were added. Feasibility SDKs live in an
isolated virtual environment outside the repository.

---

## 8. Resources created and destroyed

| Resource | Purpose | Status |
|---|---|---|
| S3 bucket `xc-feasibility-s3-9fe354f09cfa` | F6 protocol proof | **Deleted** (9 versions removed; absence verified) |
| Cognito user pool `xc-feasibility-f10` | F10 auth proof | **Deleted** (absence verified) |
| Cognito users `f10-admin`, `f10-viewer` | role claim proof | Deleted with the pool |
| AgentCore stack `AgentCore-xcfeasibility-default` | F8 | **Deployed 2026-09-21 by the owner, then torn down the same day** by owner decision (§9) — `aws cloudformation delete-stack`; runtime, memory, and IAM roles confirmed absent |
| Cognito pool, API Gateway HTTP API, 1 Lambda, 1 IAM role `xc-feasibility-f10-*` | F10 protected-API proof | **Deleted** (pool, API, function, role/policy all confirmed absent) |

Live API calls: 152 RunSignup requests (all HTTP 200, sequential, within the
documented 2-concurrent-call guidance), ~7 Tavily operations, 34 Bedrock model
invocations from the model evaluation, plus 6 live AgentCore Runtime
invocations during F8 verification, and the S3/Cognito operations above.

---

## 9. Owner decision — RECORDED, 2026-09-21

Per the spec, Task 4 could not begin until this report was approved. It now
has been.

| # | Decision | Outcome |
|---|---|---|
| 1 | Go/no-go on proceeding to Task 4 | **GO.** Approved as recommended — 7/10 gates clean PASS, 1 PASS with caveat, 1 removed (cost), the remaining two resolved with conditions below. |
| 2 | AgentCore stack (`AgentCore-xcfeasibility-default`) disposition | **Torn down.** Deleted via `aws cloudformation delete-stack` (a single discrete call, not a bulk CDK apply); confirmed absent — no runtime, memory, or IAM role remains. Redeploying for Task 11.5 is fast (~4 min) and cheap. |
| 3 | F5 — non-RunSignup extraction fallback | **Dropped from release 1 entirely**, not merely shipped experimental. No genuine non-RunSignup source produced usable rows during Task 3.3. Requirement 6, design §3.2/§9.2/§9.4, Task 9, and the `xc-tavily-source-routing` skill were all amended accordingly — release 1 ingests RunSignup-family sources only (including the two confirmed white-label mirrors). A future release may reopen this against a specifically approved and measured source. |
| 4 | Model selection (Haiku 4.5 default) and resolution policy (review-only) | **Not yet finalized** — the owner wants to discuss further before confirming. Task 4 (schema/migrations) does not depend on either choice, so it is not blocked by this; revisit before Task 11 implementation. |

**Consequence of decision 2 for gate F8:** actor isolation remains unverifiable
until a `CUSTOM_JWT` authorizer and Cognito user pool exist (Task 15.2), and
now additionally requires a fresh `agentcore deploy` since the stack was torn
down. Do not enable multi-user agent access before both exist and isolation is
retested.

**Consequence of decision 3 for the spec:** every mention of non-RunSignup
extraction fallback across requirements.md (R5.7, R6, R17.5), design.md
(executive summary, §3.2, §9.2, §9.4, the F5 gate row), tasks.md (Task 9's
title, 9.1, 9.3 marked removed, 9.4), and the `xc-tavily-source-routing` skill
was amended on 2026-09-21 to reflect the actual, narrower release-1 scope —
not just noted here.
