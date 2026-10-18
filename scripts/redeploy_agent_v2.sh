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
# Run from WSL at the repo root, with Node 20.19.1 active (nvm use 20.19.1)
# and AWS_CA_BUNDLE set (your .bashrc does it). Nothing needs activating:
#   - the CDK app runs on infrastructure/cdk/.venv-wsl (XC_CDK_VENV), the
#     runbook's WSL venv with aws-cdk-lib;
#   - steps 4-5 run on .venv-deploy (XC_DEPLOY_VENV), which the script
#     creates on first use with boto3/botocore 1.43.99 -- the lock's pin,
#     new enough to send platformVersion.
#
# Usage:
#   scripts/redeploy_agent_v2.sh [--model <inference-profile-id>]
#       [--effort low|medium|high] [--tag <image-tag>] [--allow-dirty]
#       [--yes] [--preflight-only]
#
# --model/--effort default to the stack's defaults (Sonnet 4.6, medium).
# --tag defaults to the short commit hash; a dirty tree is refused unless
# --allow-dirty, so every tag maps to committed code. --preflight-only
# runs every check (and sets up .venv-deploy) without building anything.

set -euo pipefail

EXPECTED_ACCOUNT="${XC_EXPECTED_ACCOUNT:-918221680168}"
REGION="${AWS_REGION:-us-east-1}"
CDK="npx aws-cdk@2.1032.0"
STACK="XcPlatform-Agent"
BOTO_PIN=1.43.99  # requirements.lock's boto3/botocore
CDK_VENV="${XC_CDK_VENV:-infrastructure/cdk/.venv-wsl}"
DEPLOY_VENV="${XC_DEPLOY_VENV:-.venv-deploy}"
NODE_VERSION=20.19.1

MODEL=""
EFFORT=""
TAG=""
ALLOW_DIRTY=0
APPROVAL=()
PREFLIGHT_ONLY=0

while [[ $# -gt 0 ]]; do
  case "$1" in
    --model) MODEL="$2"; shift 2 ;;
    --effort) EFFORT="$2"; shift 2 ;;
    --tag) TAG="$2"; shift 2 ;;
    --allow-dirty) ALLOW_DIRTY=1; shift ;;
    --yes) APPROVAL=(--require-approval never); shift ;;
    --preflight-only) PREFLIGHT_ONLY=1; shift ;;
    -h|--help) sed -n '2,28p' "$0"; exit 0 ;;
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
  fail "uncommitted changes in agent sources; commit them or pass --allow-dirty
(in WSL, a tree that is clean on Windows looks dirty unless: git config core.autocrlf true)"
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

NODE_FOUND="$(node --version 2>/dev/null || echo none)"
[[ "$NODE_FOUND" == "v$NODE_VERSION" ]] \
  || fail "Node $NODE_FOUND is active; run: nvm use $NODE_VERSION"

# The CDK app (cdk.json: "python app.py") needs aws-cdk-lib. It runs on the
# runbook's WSL venv; infrastructure/cdk/.venv is the Windows one.
"$CDK_VENV/bin/python" -c "import aws_cdk, constructs" 2>/dev/null \
  || fail "$CDK_VENV has no aws-cdk-lib. Create it once:
  python3 -m venv $CDK_VENV && $CDK_VENV/bin/python -m pip install -r infrastructure/cdk/requirements.txt"

# Steps 4-5 need a boto3 that can send platformVersion. The script owns this
# venv and builds it on first use; pip goes through the same CA bundle as the
# AWS CLI, because Norton inspects TLS on this machine.
PYTHON="$DEPLOY_VENV/bin/python"
boto_ok() {
  "$PYTHON" - "$REGION" 2>/dev/null <<'PY'
import sys

import boto3

op = boto3.client("bedrock-agentcore-control", region_name=sys.argv[1]).meta
assert "platformVersion" in op.service_model.operation_model(
    "UpdateAgentRuntime"
).input_shape.members
PY
}
if ! boto_ok; then
  echo "setting up $DEPLOY_VENV (boto3 $BOTO_PIN) ..."
  if [[ ! -x "$PYTHON" ]]; then
    BASE_PY="$(command -v python3.12 || command -v python3)"
    "$BASE_PY" -m venv "$DEPLOY_VENV" \
      || fail "could not create $DEPLOY_VENV with $BASE_PY (apt install python3-venv?)"
  fi
  PIP_CERT="${PIP_CERT:-${AWS_CA_BUNDLE:-}}" "$PYTHON" -m pip install --quiet \
    --disable-pip-version-check "boto3==$BOTO_PIN" "botocore==$BOTO_PIN" \
    || fail "pip could not install boto3 into $DEPLOY_VENV"
  boto_ok || fail "$DEPLOY_VENV has boto3, but it cannot send platformVersion"
fi

echo "account $ACCOUNT, region $REGION, image tag $TAG"
echo "node $NODE_FOUND, CDK python $CDK_VENV, deploy python $DEPLOY_VENV"
if [[ -n "$MODEL" ]]; then echo "model $MODEL"; fi
if [[ -n "$EFFORT" ]]; then echo "effort $EFFORT"; fi
if [[ $PREFLIGHT_ONLY -eq 1 ]]; then
  echo "Preflight passed; stopping before the build (--preflight-only)."
  exit 0
fi

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
# The CDK venv's bin goes first on PATH so cdk.json's `python app.py` finds
# aws-cdk-lib -- no activation needed.
CDK_BIN="$(cd "$CDK_VENV/bin" && pwd)"
(cd infrastructure/cdk && PATH="$CDK_BIN:$PATH" $CDK deploy "$STACK" "${CONTEXT[@]}" "${APPROVAL[@]}")

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
