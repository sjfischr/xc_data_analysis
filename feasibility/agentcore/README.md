# AgentCore feasibility prototype (Task 3.5, gate F8)

A minimal Strands agent packaged for Bedrock AgentCore, used to prove the
runtime contract before the Task 3.8 gate. **Not production code** — the
production agents live under `src/xc_platform/agents` after the gate passes.

## Status

Built, validated, and **not deployed**. CDK synthesis and bootstrap checks pass;
the apply awaits explicit owner approval because it creates IAM roles.

## Prerequisites

The npm CLI, not the Python starter toolkit (which is deprecated and whose
`deploy --help` crashes):

```bash
npm install -g @aws/agentcore
```

On a machine with TLS interception (for example Norton), these are required or
the CDK build fails fetching from PyPI with `CERTIFICATE_VERIFY_FAILED`:

```bash
export AWS_CA_BUNDLE="C:\\ProgramData\\Norton\\Antivirus\\wscert.pem"
export SSL_CERT_FILE="$AWS_CA_BUNDLE"
export UV_NATIVE_TLS=1
```

## Rebuild the bundled snapshot

The snapshot is a build artifact (gitignored), regenerated from the frozen
baseline CSV:

```bash
python -c "
import sys; sys.path.insert(0,'../../src')
from pathlib import Path
from xc_platform.feasibility.s3_protocol import build_representative_snapshot
info = build_representative_snapshot(
    Path('xcfeasibility/app/xcanalytics/data/xc-snapshot.db'),
    Path('../../data/merged/season_results.csv'))
Path('xcfeasibility/snapshot.sha256').write_text(info['sha256'])
print(info['rows'], 'rows;', info['sha256'])
"
```

Then put that digest in `agentcore/agentcore.json` under the runtime's
`XC_SNAPSHOT_SHA256` env var, so the deployed agent verifies the snapshot before
opening it. Without a matching digest the agent still runs but reports
`sha256_verified: false` — deliberately visible rather than silently skipped.

## Deploy

```bash
cd xcfeasibility
agentcore deploy          # interactive; add --yes for non-interactive
agentcore invoke "How many race results are in this dataset?"
agentcore logs
agentcore destroy         # remove everything when the probe is finished
```

## What the stack creates

| Resource | Notes |
|---|---|
| `AWS::BedrockAgentCore::Runtime` | Python 3.14, CodeZip, `PUBLIC` network mode, OTel enabled |
| `AWS::BedrockAgentCore::Memory` | 30-day event expiry, **no long-term strategies** (short-term only) |
| 2 × `AWS::IAM::Role` | both trust `bedrock-agentcore.amazonaws.com` |
| 1 × `AWS::IAM::Policy` | Bedrock invoke, CloudWatch Logs, X-Ray, AgentCore memory |

## What still needs proving once deployed

`/ping` behavior, streaming, session isolation, memory actor isolation and
deletion, cold-start latency, and observability output — the parts of gate F8
that cannot be verified without a live runtime.
