# Infrastructure as code (Task 15)

A real, `cdk synth`-verified CDK Python app. **Never deployed by this
codebase** -- consistent with this session's owner decision to write and
test infrastructure/agent code without making any live AWS call. Everything
here is proven at the synthesis/assertion level (`test_storage_stack.py`),
not the live-deployment level.

## Scope: what's here vs. what isn't

Only `stacks/storage_stack.py` (Task 15.1's storage + async-processing
half) is built: the private encrypted/versioned data and web S3 buckets
(deny-insecure-transport bucket policies, full public-access block), the
ingest SQS FIFO queue with a dead-letter queue and bounded retries, and a
least-privilege IAM role for the (not-yet-built) import worker Lambda.

**Not built** (an honest scope cut, not an oversight -- see tasks.md Task
15's header note): 15.2 (CloudFront, the protected web/API origins, HTTP
API + Lambda, WebSocket API + Lambda, Cognito), 15.3 (AgentCore Runtime and
Memory resources, Bedrock model access, SSM parameters, tracing/dashboards/
alerts), and the reserved-concurrency-one import worker Lambda function
itself (only its IAM role exists so far). Each of these is a substantial
build in its own right; building all of them to the same synthesis-tested
standard as the storage stack did not fit this session's remaining time
after Tasks 10-14. AgentCore specifically also already has its own
CDK-generating tool (the `agentcore` CLI used by
`feasibility/agentcore/`) that a production AgentCore stack would most
naturally extend, rather than hand-rolling a second, parallel CDK
definition for the same resources here.

15.4 (runaway protection) is **already implemented in application code**,
not infrastructure: `xc_platform.agents.analytics_agent.DEFAULT_LIMITS`/
`run_turn` enforce per-request token/tool-iteration caps (tested,
`agents/test_runaway_protection.py`), and
`xc_platform.ingest.adapters.tavily_client.TavilyBudget` (Task 9) already
enforces per-import request/credit/wall-time limits. There is nothing
further to add here for that requirement.

## Setup

A dedicated virtualenv, deliberately separate from the main project's
`.venv` (aws-cdk-lib has no reason to be importable from `xc_platform`'s
own runtime):

```bash
python -m venv .venv
.venv/Scripts/pip install -r requirements.txt
```

## Commands

```bash
# Synthesize CloudFormation from the CDK app -- no AWS credentials needed,
# nothing is deployed. Requires Node (already pinned at the repo root,
# web/package.json) and downloads the `aws-cdk` CLI via npx on first run.
PATH="$PWD/.venv/Scripts:$PATH" npx --yes aws-cdk@2.1032.0 synth

# Infrastructure synthesis tests + policy assertions (Task 15.5) -- pure
# Python, no Node/npx needed.
.venv/Scripts/python -m pytest test_storage_stack.py -q
```

## What was actually verified this session

- `cdk synth` produces a valid CloudFormation template (`cdk.out/
  XcPlatform-Storage.template.json`, gitignored as a build artifact).
- Both buckets: `BlockPublicAccess.BLOCK_ALL`, S3-managed encryption,
  versioning enabled, and a bucket policy denying every principal when
  `aws:SecureTransport` is false (confirmed in the synthesized template,
  not just asserted from the CDK construct call).
- The ingest queue is FIFO, redrives to a FIFO dead-letter queue after 3
  receives, and the DLQ retains messages for 14 days.
- The import worker's IAM policy has no wildcard `Resource: "*"` grant --
  checked programmatically against the synthesized template, not just
  read by eye.

## What was NOT verified (because nothing was deployed)

Actual AWS behavior: whether the bucket policies/IAM role work as intended
against a real account, real least-privilege boundary testing beyond
"no wildcard resource," and everything Task 15.5 asks for beyond synthesis
(a staging deployment smoke test, proving no public path reaches real
data). That needs an actual `cdk deploy`, which is explicitly out of scope
for this session.
