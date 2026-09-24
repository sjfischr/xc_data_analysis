---
name: xc-strands-limits-portability
description: 'Use when working on Strands agent execution limits, per-request token/iteration caps, model routing or escalation policy, hooks/auditing, or runtime-neutral packaging (CLI/AgentCore/in-process adapters) for the XC platform.'
---

# Execution limits, hooks, and model portability

> Status: **illustrative pseudocode** — not yet validated against a pinned
> Strands SDK version (Task 3 records validated versions).
> Docs checked 2026-09-20: [Strands documentation](https://strandsagents.com/latest/documentation/docs/),
> [Model providers](https://strandsagents.com/latest/documentation/docs/user-guide/concepts/model-providers/amazon-bedrock/),
> [Hooks](https://strandsagents.com/latest/documentation/docs/user-guide/concepts/agents/hooks/).

## Mandatory execution limits

- **Per-request caps**: maximum agent-loop iterations and a per-request token
  ceiling. Exceeding either returns a typed "limit exceeded" response — never
  a silent continuation.
- **Monthly budgets**: *(removed 2026-09-20 by owner decision — cost is no
  longer a requirement or a gate; do not reintroduce it.)*
- **No automatic escalation**: a failed call must NOT be retried on a more
  different model after a policy rejection. *(The USD 15/20 cost thresholds
  were removed on 2026-09-20.)*
- **Model IDs must be inference profiles** (`us.`-prefixed). Bare model IDs are
  rejected by Bedrock with "on-demand throughput isn't supported" (Task 3.6).

## Model routing policy

1. Default to a **low-cost model** for planning, tool orchestration, and
   summaries.
2. Route to the high-capability model only when a **deterministic need check**
   (e.g., case complexity score, explicit user request for deep analysis)
   passes — never on model self-assessment.
3. Model IDs live in configuration, not code, so the same agent runs on
   different Bedrock models (and local providers in dev) without edits.

## Hooks: audit everything, hide nothing sensitive

Use Strands hooks (before/after tool invocation, message added) to:

- attach correlation IDs (`request_id`, `agent_session_id`, `tool_call_id`);
- record tool name, argument summary, duration, and outcome as usage events;
- pass every logged string through `xc_platform.security.redaction` first;
- count tokens/iterations toward the request budget and abort at the cap.

Do not store hidden chain-of-thought; log only tool traffic and final
responses.

## Runtime-neutral packaging

- Core agents live in `src/xc_platform/agents` with **no** FastAPI, CLI, or
  AgentCore imports; they depend only on typed tools and stdlib-level libs
  (no pandas / compiled deps — AgentCore runs Linux/ARM).
- Three thin adapters bind the same core:
  1. local CLI (`xc_platform.cli`) for development and testing;
  2. AgentCore entrypoint (see the `xc-agentcore-runtime-deployment` skill);
  3. emergency in-process mode inside the API service (dev/fallback only).

## Anti-patterns

- Letting the model choose its own model/temperature/budget.
- Retrying failed generations on a pricier model "to be helpful".
- Catch-all hooks that log raw payloads without redaction.
- Importing runtime frameworks into `xc_platform.agents`.
