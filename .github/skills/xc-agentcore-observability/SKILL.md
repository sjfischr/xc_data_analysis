---
name: xc-agentcore-observability
description: 'Use when working on agent observability or runaway protection: correlation IDs, usage events, structured traces, and per-request token/iteration caps for the XC platform. Cost limits were removed from the spec on 2026-09-20.'
---

# Observability and cost controls

> Status: **design policy** (normative for this project) with illustrative
> snippets — not yet validated live (Task 3).
> Docs checked 2026-09-20:
> [AgentCore Observability](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/observability.html),

## Correlation IDs (mandatory on every event)

Propagate this ID set through API → agent → tools → database access:

`request_id`, `ingest_run_id`, `agent_session_id`, `tool_call_id`,
`publication_id`.

Every usage event, log line, and error report carries the applicable subset,
after passing through `xc_platform.security.redaction`. Pinning
`publication_id` per request makes any answer reproducible against the exact
snapshot that produced it.

## What is (and is not) recorded

- Recorded: tool calls (name, argument summary, duration, outcome), token
  usage, model ID, final responses.
- **Not recorded**: hidden chain-of-thought, raw credentials, raw fetched web
  content beyond provenance-labeled stored payloads.

## Runaway protection (cost is NOT a constraint)

> **Amended 2026-09-20 by owner decision.** The USD 20 ceiling, AWS Budget
> alerts, and soft/hard cost limits were removed from the spec. Do not
> reintroduce cost as a requirement, acceptance criterion, or gate.

| Control | Limit | Behavior on breach |
|---|---|---|
| Per-request tokens | configured per agent | stop that request, explanatory status |
| Per-request tool iterations | configured per agent | stop that request, explanatory status |
| Per-import fetch pages/depth/requests | configured per run | pause run, resumable |

A runaway stop never takes authenticated read-only dashboards offline.
Usage telemetry (tokens, latency, tool calls) is still recorded under
observability — visible, but never enforced or gating.


## Anti-patterns

- Cost tracking from memory or vibes — pricing facts must come from current
  AWS pricing pages/APIs at feasibility time (Task 3), never recalled.
- Logging raw prompts/responses with credentials or full youth rosters.
- Silent retries after budget rejection.
- Metrics without correlation IDs (unattributable spend or errors).
