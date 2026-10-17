#!/usr/bin/env bash
# Redeploy the Ask the Data agent to AgentCore Runtime on platform V2.
#
# Builds the arm64 agent image under a new tag, pushes it, deploys the
# XcPlatform-Agent stack pointing at that tag, then makes sure the runtime
# is on platformVersion V2 (design.md 12.6). CDK/CloudFormation can't set
# platformVersion yet, so step 4 calls UpdateAgentRuntime through
# xc_platform.cli.agentcore_platform_version; it is a no-op when the
# runtime is already on V2, and later CDK updates leave V2 in place.
#
# Run from WSL at the repo root, after the runbook's routine-update setup:
#   export AWS_CA_BUNDLE=/mnt/c/Users/sjfis/.aws/ca-bundle-with-norton.pem
#   nvm use 20.19.1
#   source infrastructure/cdk/.venv/bin/activate   # CDK app's Python
#
# Usage:
#   scripts/redeploy_agent_v2.sh [--model <inference-profile-id>]
#       [--effort low|medium|high] [--tag <image-tag>] [--allow-dirty] [--yes]
#
# --model/--effort default to the stack's defaults (Sonnet 4.6, medium).
# --tag defaults to the short commit hash; a dirty tree is refused unless
# --allow-dirty, so every tag maps to committed code.

set -euo pipefail

EXPECTED_ACCOUNT="${XC_EXPECTED_ACCOUNT:-918221680168}"
REGION="${AWS_REGION:-us-east-1}"
CDK="npx aws-cdk@2.1032.0"
STACK="XcPlatform-Agent"
PYTHON="${XC_PYTHON:-.venv-wsl/bin/python}"

MODEL=""
EFFORT=""
TAG=""
ALLOW_DIRTY=0
APPROVAL=()

while [[ $# -gt 0 ]]; do
  case "$1" in
    --model) MODEL="$2"; shift 2 ;;
    --effort) EFFORT="$2"; shift 2 ;;
    --tag) TAG="$2"; shift 2 ;;
    --allow-dirty) ALLOW_DIRTY=1; shift ;;
    --yes) APPROVAL=(--require-approval never); shift ;;
    -h|--help) sed -n '2,25p' "$0"; exit 0 ;;
    *) echo "unknown argument: $1" >&2; exit 2 ;;
  esac
done

step() { printf '\n==> %s\n' "$*"; }
fail() { echo "error: $*" >&2; exit 1; }

[[ -f Dockerfile.agent && -d infrastructure/cdk ]] || fail "run from the repo root"

step "Preflight"
ACCOUNT=$(aws sts get-caller-identity --query Account --output text) \
  || fail "no AWS credentials (is AWS_CA_BUNDLE set?)"
[[ "$ACCOUNT" == "$EXPECTED_ACCOUNT" ]] \
  || fail "signed in to account $ACCOUNT, expected $EXPECTED_ACCOUNT"
if [[ $ALLOW_DIRTY -eq 0 && -n "$(git status --porcelain -- src migrations Dockerfile.agent requirements-agent.lock infrastructure)" ]]; then
  fail "uncommitted changes in agent sources; commit them or pass --allow-dirty"
fi
# Built in steps: an assignment takes the status of its last command
# substitution, so a `$([[ ... ]] && echo -dirty)` that prints nothing
# would return 1 and trip set -e on a clean tree.
if [[ -z "$TAG" ]]; then
  TAG="$(git rev-parse --short HEAD)"
  if [[ $ALLOW_DIRTY -eq 1 && -n "$(git status --porcelain)" ]]; then
    TAG="$TAG-dirty"
  fi
fi
docker buildx version >/dev/null || fail "docker buildx is not available"
# Steps 4-5 need a boto3 new enough to send platformVersion. The CDK venv
# has none; .venv-wsl gets one with (same pin as requirements.lock):
#   python3 -m venv .venv-wsl && .venv-wsl/bin/pip install boto3==1.43.99
"$PYTHON" -c "
import boto3
op = boto3.client('bedrock-agentcore-control', region_name='$REGION').meta \
    .service_model.operation_model('UpdateAgentRuntime')
assert 'platformVersion' in op.input_shape.members
" 2>/dev/null || fail "$PYTHON lacks a boto3 that supports platformVersion; run:
  python3 -m venv .venv-wsl && .venv-wsl/bin/pip install boto3==1.43.99
or set XC_PYTHON to a Python that has it"
echo "account $ACCOUNT, region $REGION, image tag $TAG"
if [[ -n "$MODEL" ]]; then echo "model $MODEL"; fi
if [[ -n "$EFFORT" ]]; then echo "effort $EFFORT"; fi

step "1/5 Build and push the arm64 image"
AGENT_REPO=$(aws cloudformation describe-stacks --stack-name "$STACK" --region "$REGION" \
  --query "Stacks[0].Outputs[?OutputKey=='AgentRepositoryUri'].OutputValue" --output text)
[[ -n "$AGENT_REPO" && "$AGENT_REPO" != "None" ]] || fail "no AgentRepositoryUri output on $STACK"
aws ecr get-login-password --region "$REGION" \
  | docker login --username AWS --password-stdin "${AGENT_REPO%%/*}"
# On plain Docker Engine in WSL, arm64 emulation needs (once):
#   docker run --privileged --rm tonistiigi/binfmt --install arm64
docker buildx build --platform linux/arm64 -f Dockerfile.agent \
  -t "$AGENT_REPO:$TAG" --push .

step "2/5 Confirm the image is in ECR"
aws ecr describe-images --region "$REGION" \
  --repository-name "${AGENT_REPO#*/}" --image-ids imageTag="$TAG" \
  --query "imageDetails[0].[imageTags,imagePushedAt]" --output text

step "3/5 Deploy $STACK with the new image"
CONTEXT=(-c "agentImageTag=$TAG")
if [[ -n "$MODEL" ]]; then CONTEXT+=(-c "agentModelId=$MODEL"); fi
if [[ -n "$EFFORT" ]]; then CONTEXT+=(-c "agentEffort=$EFFORT"); fi
(cd infrastructure/cdk && $CDK deploy "$STACK" "${CONTEXT[@]}" "${APPROVAL[@]}")

RUNTIME_ARN=$(aws cloudformation describe-stacks --stack-name "$STACK" --region "$REGION" \
  --query "Stacks[0].Outputs[?OutputKey=='AgentRuntimeArn'].OutputValue" --output text)
RUNTIME_ID="${RUNTIME_ARN##*/}"
[[ -n "$RUNTIME_ID" && "$RUNTIME_ID" != "None" ]] || fail "no AgentRuntimeArn output on $STACK"

step "4/5 Ensure platform V2 on $RUNTIME_ID (a V2 update takes several minutes)"
PYTHONPATH=src "$PYTHON" -m xc_platform.cli.agentcore_platform_version "$RUNTIME_ID" \
  --platform-version V2 --region "$REGION" --poll-timeout 1200

step "5/5 Verify"
PYTHONPATH=src "$PYTHON" - "$RUNTIME_ID" "$REGION" "$TAG" <<'PY'
import sys

import boto3

runtime_id, region, tag = sys.argv[1:4]
r = boto3.client("bedrock-agentcore-control", region_name=region).get_agent_runtime(
    agentRuntimeId=runtime_id
)
uri = r["agentRuntimeArtifact"]["containerConfiguration"]["containerUri"]
env = r.get("environmentVariables", {})
print(f"status          {r['status']}")
print(f"platformVersion {r.get('platformVersion')}")
print(f"image           {uri}")
print(f"XC_MODEL_ID     {env.get('XC_MODEL_ID')}")
print(f"XC_EFFORT       {env.get('XC_EFFORT')}")
problems = []
if r["status"] != "READY":
    problems.append(f"status is {r['status']}")
if r.get("platformVersion") != "V2":
    problems.append(f"platformVersion is {r.get('platformVersion')}")
if not uri.endswith(f":{tag}"):
    problems.append(f"runtime is not on image tag {tag}")
if problems:
    sys.exit("FAILED: " + "; ".join(problems))
print("OK: runtime is READY on V2 with the new image")
PY

cat <<EOF

Done. Smoke test: ask a question in Ask the Data, then check the logs:
  aws logs tail /aws/bedrock-agentcore/runtimes/${RUNTIME_ID}-DEFAULT --since 10m --region $REGION
To turn the assistant off without a redeploy:
  aws ssm put-parameter --name /xc-platform/XC_AGENT_ENABLED --type String --value false --overwrite
EOF
