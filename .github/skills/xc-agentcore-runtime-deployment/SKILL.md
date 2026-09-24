---
name: xc-agentcore-runtime-deployment
description: 'Use when deploying XC agents to Amazon Bedrock AgentCore: entrypoints (@app.entrypoint, /invocations, /ping), direct-code packaging constraints (Python 3.10+, Linux/ARM, no pandas), platform version V2 snapshot-safety rules, and deployment rules.'
---

# AgentCore runtime deployment

> Status: **confirmed live** (Task 3.5, 2026-09-21 — deploy, `/ping`,
> invocation, streaming, packaging, observability all verified against a real
> runtime; actor isolation requires Task 11.5/15.2's Cognito `CUSTOM_JWT`
> authorizer and is not closeable in the feasibility environment).
> Platform version V2 is a 2026-09-22 owner decision (design.md §12.6),
> confirmed via docs, not yet exercised against a live V2 runtime.
> Docs checked 2026-09-22:
> [What is Amazon Bedrock AgentCore](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/what-is-bedrock-agentcore.html),
> [AgentCore Runtime](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/agents-tools-runtime.html),
> [Platform versions](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/runtime-how-it-works.html#runtime-platform-versions),
> [Optimize for V2](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/runtime-v2-optimize.html).

## Packaging constraints (why the agents package is lean)

- AgentCore direct-code deployment targets **Python 3.10+** on **Linux/ARM**.
- `src/xc_platform/agents` therefore avoids pandas and other compiled
  dependencies; analytics math happens in SQL against the snapshot.
- The same core agent must run unchanged under the local CLI adapter — the
  AgentCore adapter is a thin wrapper only.

## Entrypoint contract

Confirmed live (Task 3.5) against `bedrock_agentcore.runtime.BedrockAgentCoreApp`.
Module-scope code runs once, before `app.run()` opens port 8080; the
`@app.entrypoint` function runs fresh on every request. See
[feasibility/agentcore/xcfeasibility/app/xcanalytics/main.py](../../../feasibility/agentcore/xcfeasibility/app/xcanalytics/main.py)
for the full working example this pattern is drawn from.

```python
from bedrock_agentcore.runtime import BedrockAgentCoreApp
from xc_platform.agents import build_analytics_agent

app = BedrockAgentCoreApp()

# Module scope: runs once, captured in the V2 snapshot, shared by every
# restored instance. Only put things here that are identical for the life
# of this runtime version -- see "Platform version V2" below.
agent = build_analytics_agent()  # runtime-neutral core

@app.entrypoint
def invoke(payload: dict, context) -> dict:
    # Per-request: runs fresh on every call, on every instance. Identity,
    # randomness, current time, and credentials belong here, not above.
    return agent.handle(payload, context)

app.run()  # starts listening on 8080; the V2 snapshot is taken after this
```

Or the raw HTTP contract: serve `POST /invocations` (JSON in/out) and
`GET /ping` for health, matching the AgentCore runtime service contract.

## Platform version V2 (design.md §12.6)

Every production runtime is deployed with `platformVersion=V2`: consistent
cold starts regardless of image size or concurrency, and lower cost for
idle/bursty agents, because AgentCore Runtime prepares the environment once,
snapshots it, and restores that snapshot per instance instead of
re-initializing on every cold start.

**The `agentcore` CLI cannot set it.** It deploys through a generated CDK
app (`agentcore.json`: `"managedBy": "CDK"`), and CDK/CloudFormation do not
support `platformVersion` yet. After `agentcore deploy`, run
`python -m xc_platform.cli.agentcore_platform_version <agent-runtime-id>` to
set V2 via a direct `bedrock-agentcore-control` `UpdateAgentRuntime` call
(boto3, pinned in Task 6). This is safe to run once: AWS confirms a later
CDK-driven update that omits `platformVersion` leaves the runtime on
whatever platform version it already has.

**Startup vs. per-request split.** Apply one rule: a value computed at
startup is frozen into the snapshot and identical on every restored
instance, so it belongs there only if it is genuinely startup-stable.

| Startup (module scope, before `app.run()`) | Per request (inside the handler) |
|---|---|
| Imports, model weights, static config | `os.urandom()`/`secrets`/`uuid.uuid4()` -- a value read at startup repeats on every restored instance |
| The pinned approved snapshot, verified and opened once | The current time and any elapsed-time reference (`time.monotonic()` does not advance across a restore) |
| Reusable clients, ideally exercised with a warm-up call so their cached connection setup is captured too | Credentials/tokens, refreshed on expiry, never cached from startup |
| | Per-instance/worker identifiers -- every restored instance reports the same hostname (`localhost`) and PID (`1`) |

**Other V2 operational facts:** create/update takes minutes (poll
`GetAgentRuntime` for `READY`/`*_FAILED`, do not assume synchronous
completion); `/ping` must report healthy within 120 seconds of startup, and
only after initialization finishes, since the snapshot is taken on the
first healthy response; environment variables are capped at 1.5 KB for
direct-code deployments (vs. 4 KB on V1) -- prefer the deployment bundle's
static config file for anything larger; V2 is available in `us-east-1`,
`us-east-2`, `us-west-2`, `eu-west-1`, `ap-northeast-1` (this project
deploys to `us-east-1`).

## Deployment rules

1. **Feasibility first**: no AgentCore resource is created outside the Task 3
   harness until the go/no-go gate (owner decision)
   passes.
2. Deployments are made by CI/IaC (`infrastructure/`), not from laptops; the
   agent runtime role is separate from ingestion and API roles (least
   privilege).
3. Pin the `bedrock-agentcore` SDK and `strands` versions exactly in the
   locked requirements; record validated versions in the feasibility report.
4. The emergency fallback is the in-process dev mode behind the API — do not
   build a second bespoke runtime.
5. Every deploy sets `platformVersion=V2` via
   `xc_platform.cli.agentcore_platform_version` and confirms `READY` before
   routing traffic to the new version.

## Anti-patterns

- Giving the runtime role S3 write access to `database/active.json` (only the
  publication writer path may swap manifests).
- Baking credentials or model IDs into the image/code — use runtime
  configuration and AWS-injected identity.
- Divergent local vs deployed agent behavior (any divergence means the
  adapter grew logic that belongs in the core).
- Reading a random value, timestamp, credential, or instance identifier at
  module scope under V2 — it is captured once in the snapshot and reused,
  identically, by every restored instance.
- Assuming a V2 `agentcore deploy`/CDK update completed synchronously, or
  that it preserved V2 without the follow-up `UpdateAgentRuntime` call on a
  brand-new runtime (a fresh `CreateAgentRuntime` defaults to V1 if
  `platformVersion` is never set at all).
