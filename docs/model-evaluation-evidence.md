# Model quality, latency, and cost evidence (Task 3.6, gate F7)

**Observed:** September 20, 2026, `us-east-1`, account `918221680168`.
**Evidence:** `feasibility/runs/<run-id>-model-eval/` and
`<run-id>-model-eval-documented/`.

Models compared, both via **inference profile IDs**:

| Tier | Model |
|---|---|
| Lower-cost | `us.anthropic.claude-haiku-4-5-20251001-v1:0` |
| Higher-capability | `us.anthropic.claude-sonnet-4-5-20250929-v1:0` |

## Inference profiles are mandatory

Invoking the bare model ID `anthropic.claude-haiku-4-5-20251001-v1:0` fails:

> Invocation of model ID ... with on-demand throughput isn't supported. Retry
> your request with the ID or ARN of an inference profile that contains this
> model.

The `us.` prefixed inference profile works. **Model routing (design §12.5) must
configure inference-profile IDs, not raw model IDs**, or every agent call fails
at runtime.

## Gate F7 verdict

| F7 criterion | Haiku 4.5 | Sonnet 4.5 | Verdict |
|---|---|---|---|
| Confirmed-distinct separation = 100% | 4/4, **0 false merges** | 4/4, **0 false merges** | **PASS** |
| Analytical answers match fixtures | 7/7 | 7/7 | **PASS** |
| Ambiguous pairs routed to review | 2/4 | 2/4 | **FAIL** |

The first two criteria are the gate's stated pass conditions, and both models
meet them. The third is the reason auto-match stays disabled (below).

## Analytics fixtures: 7/7 — after a harness defect was fixed

The first run scored 5/7 for **both** models. Investigating rather than
reporting the number:

| Fixture | Expected | Model answered | Cause |
|---|---|---|---|
| `distinct_athletes` | 1,440 | 1,441 | **The data, not the model** |
| `distinct_teams` | 27 | 2 | **My harness, not the model** |

- **1,441 vs 1,440:** the snapshot contains exactly **one row with an empty
  athlete name**. The model counted the raw table correctly; my ground-truth SQL
  excluded the blank. This is a genuine data-quality finding and a quarantine
  candidate for Task 5.1.
- **27 vs 2:** the feasibility snapshot carries positional column names
  (`c0`…`c55`) straight from the baseline CSV. The model picked `c10`
  (`meet_series`, 2 distinct values) instead of `c6` (`team_name`, 27). With no
  schema description it had no way to know.

Adding a column glossary to the `describe_dataset` tool and re-running gave
**7/7 on both models**, isolating the cause cleanly.

**This is the strongest argument in the probe for the design's own decision**
(§11.3): agents should get **typed, metric-specific tools**, not raw SQL over an
undocumented schema. `run_readonly_query` is a constrained escape hatch, and the
failure mode when schema context is missing is a confidently wrong number, not
an error. Task 11.1 should treat rich schema description as a correctness
requirement.

## Resolution decisions: zero false merges, but conservative routing fails

Ten curated cases from `name_corrections.csv` and `DUPLICATE_NAMES_REPORT.md`.

**Confirmed-distinct (4 sibling pairs) — both models kept all four separate.**
Neither ever merged Gianna/Giovanni Smolinski, Luke/Blake Pleva,
Anna/Adam Shewangzaw, or Carly/Charlie Buechel. This is the release gate and it
holds.

**Ambiguous (4 pairs) — both models scored 2/4.** Both returned `MATCH` for
Kaitlyn/Katelynn Brown and `CREATE` for Elise/Emilie Fitzgibbon, where the
curated decision is `REVIEW`. Near-identical spellings get merged confidently;
similar-but-distinct given names get split confidently. Neither is a
confirmed-distinct violation, but both are decisions a human reserved.

**Confirmed-same (2 pairs) — Haiku 2/2, Sonnet 0/2.** Sonnet returned `REVIEW`
for Gwen→Gwendolyn Fischer and Charlie→Charlotte Kennedy, reasoning that they
"could also be siblings." The higher-capability model was *more* conservative
and therefore scored lower against the curated set while erring in the safe
direction.

**Consequence:** keep the resolution agent **review-only**. Do not enable an
auto-match threshold on this evidence. The curated `apply` corrections should be
seeded as approved aliases (Task 5.2) so they never need a model call at all —
which also removes the cases Sonnet is most hesitant about.

## Measured cost inputs

| Metric | Haiku 4.5 | Sonnet 4.5 |
|---|---|---|
| Input tokens (17 evaluations) | 233,645 | 189,073 |
| Output tokens | 9,977 | 8,910 |
| Total tokens | 243,622 | 197,983 |
| Tool calls | 13 | 13 |
| Latency p50 | 1.11 s | 2.31 s |
| Latency max | 4.89 s | 9.81 s |
| Errors | 0 | 0 |

Sonnet used *fewer* tokens (it re-queried less) but was ~2× slower per call.

Input token counts are dominated by tool-result payloads and conversation
growth, not by the questions. Trimming tool output (the 200-row cap already
helps) is the main lever on cost.

## Pricing could not be retrieved programmatically

Two authoritative sources were tried:

1. **AWS Pricing API** (`aws pricing get-products --service-code AmazonBedrock`):
   1,058 products scanned across 11 pages; the only Anthropic entries are
   **Claude 2.0, 2.1, Instant, 3 Haiku, and 3 Sonnet**. No 4.5-generation model
   pricing is published there.
2. **The Bedrock pricing page**: prices are rendered client-side as
   `{priceOf!bedrockfoundationmodels/...}` placeholders, so the HTML carries no
   figures.

Project guidance forbids asserting pricing from memory, so
`estimate_monthly_cost()` takes per-million-token prices as **inputs** and
returns `projected_usd_per_month: null` until they are supplied.

**Action required at the Task 3.8 gate:** the owner supplies current
per-million-token input/output prices for both models from the Bedrock pricing
console, and the projection completes. Measured per-question usage is already
recorded, so only two numbers per model are missing.

## Recommendation

- **Default model: Haiku 4.5.** Equal analytics accuracy (7/7), equal
  confirmed-distinct safety, better curated-set agreement (8/10 vs 6/10), and
  roughly half the latency.
- **Escalation to Sonnet 4.5: not justified by this evidence.** It was not more
  accurate on any measured dimension. Keep the escalation path configured but
  disabled, per design §12.5's policy-controlled escalation.
- **Resolution agent: review-only**, with no auto-match threshold.

---

## Task 19.2 re-evaluation: Ask the Data agent (2026-09-23)

The production analytics agent was re-evaluated after it gained its full
tool set (athlete profile, comparison, leaderboards, team what-if,
calculator, chart builder, sandboxed Python). Harness:
`scripts/agent_eval.py`, 25 cases, run live on Bedrock through the same
streaming path the product uses. Ground truth is computed from the database
at run time, not hardcoded.

The 25 cases cover:

- counts;
- Saint Sebastian leaders;
- team scoring;
- rosters;
- fastest pace;
- attendance across meets;
- progression;
- best time by distance;
- head-to-head comparison;
- a time gap;
- individual wins;
- average pace;
- a what-if;
- the scoring rule;
- a chart request;
- a significance test in Python;
- an unknown name;
- an ambiguous name;
- an out-of-scope question;
- a request to modify data;
- a prompt injection.

### Models available to this account

- Haiku 4.5, Sonnet 4.5 and Sonnet 4.6 are available.
- Sonnet 5, Opus 4.8, Opus 5 and Opus 5.5 returned `AccessDeniedException`. Enabling
  them is an owner action in the Bedrock console.
- Sonnet 5 and Opus 5 reject `temperature`. `analytics_agent.accepts_temperature`
  already omits it for those families, so they can be evaluated by setting
  `XC_MODEL_ID` once access is granted.

### Results, final configuration

| Model | Passed | Median latency | Cost for 25 cases | Per question |
|---|---|---|---|---|
| Claude Haiku 4.5 | 25/25 | 4.9 s | $0.25 | ~$0.010 |
| Claude Sonnet 4.6 | 25/25 | 9.7 s | $0.81 | ~$0.032 |

Costs are Anthropic list prices, including cache reads and writes.

### What the evaluation changed

As in F7, the tools mattered more than the model. Four fixes moved results;
changing the model did not.

1. **The SQL tool needed the schema in its description.**
   - Without it, both Haiku 4.5 and Sonnet 4.6 guessed table names until they
     hit the turn cap on every aggregation question.
   - With it, Haiku went from 6/9 to 9/9 on the gradable subset.
2. **"Team race" was ambiguous.**
   - The agent answered "which school won the most team races?" by counting
     individual winners: 4, when the real team-win count was 11.
   - Fixed with a vocabulary rule in the system prompt, plus a per-school
     `summary_by_school` on `get_team_scores_tool` so the model never counts
     rows by hand.
3. **Python needed a data path it could not skip.**
   - With an optional `sql` argument, Haiku called the sandbox with no data.
     It then pasted about 200 rows into the code and overflowed `max_tokens`.
   - `sql` is now required, and code over 6,000 characters is refused.
4. **The statistics were initially wrong.**
   - Tests treated repeated races by the same athlete as independent samples.
     That produced "significant" results: p = 0.031 on 208 race results.
   - The tool description now requires one row per athlete and prefers
     non-parametric tests on non-normal data.
   - The corrected answer is Mann-Whitney U on 50 vs 48 athletes, p = 0.137:
     not significant.

### Recommendation

- **Keep Claude Haiku 4.5 as the default.**
  - Accuracy matched Sonnet 4.6 on every case.
  - It is twice as fast, at about a third of the cost.
- Sonnet 4.6 is available if answer style ever needs it. Switch with
  `-c agentModelId=us.anthropic.claude-sonnet-4-6` on the Agent stack, with
  no code change.
- Re-run `scripts/agent_eval.py` after any prompt, tool, or model change.
  Ideally re-run it after each ingest too, since ground truth moves with the
  data.

## Task 19.2 follow-up: projections by standing (2026-09-24)

An owner test turned up an impossible answer. Asked about a 6th-grade boy's
chances, the agent fitted a line through his three raw times and projected a
3:56 mile.

### Why it happened

- Times between meets move with the course and weather, not mainly with the
  runner. Distances never change (Frosh 2 km, JV 3 km, Varsity 4 km), yet the
  2025 JV boys' median pace went 10:06, 8:17, 6:58 per mile over Meets 1-3.
- Three points are not a trend, and a straight line through them runs off to
  impossible values.

### What changed

- **"Improve" now means standing in the field** (owner decision): the share
  of the race an athlete beat, and their place. Time applies only when a user
  asks about time.
- **A tested projection model**, `standing-neighbor-change-v1`
  (`analytics/standing.py`). It takes the 80 past starters who stood nearest
  the athlete, and simulates from how their standing changed.
  - Leave-one-season-out backtest: a typical miss of about 10 percentile
    points (about 3.5 for the top 5%), slightly better than assuming no
    change.
  - Its 80% ranges held 78-81% of the time overall.
  - Two linear models were tried and rejected. One was worse; the other put a
    Meet 1 winner at about 9th.
- **Pre-written facts from the tool.** Given raw numbers, the model:
  - swapped two athletes' percentiles;
  - called 47% vs 48% "slightly better";
  - flipped who gains ground;
  - called 46% "unlikely".

  `project_standing_tool` now returns `key_facts`: a bottom line, comparisons
  in both directions, and fixed likelihood words (40-60% is "about a coin
  flip"). The system prompt requires those words.
- **Counts come from tools.** The model counted a 28-school list as 29, so
  `find_schools_tool` now returns `count`. The grader now requires "28
  schools", after "29 schools ... 28 named" passed a bare "28" check.
- **An impossible-pace check** now runs on every eval answer: any pace under
  4:00/mi fails.

### Results

- The eval now has 27 cases.
- New projection cases (`improve_chance`, `hold_lead`): 6/6 over three
  repeated runs.
- `schools`: 3/3 after the count fix.
- One `avg_pace` run looped until it ran out of steps; that case passed 3/3
  on re-run.
