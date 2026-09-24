# Task 19 deploy runbook: new site, Ask the Data, intake, alarms

The owner runs these from WSL in `/mnt/c/Dev/xc_data_analysis`, with the usual
AWS profile. Account 918221680168, region us-east-1.

Order matters. The agent runtime needs an image before it can exist, and the
web stack reads the runtime's ARN.

## 0. One-time settings

```bash
export AWS_REGION=us-east-1 CDK_DEFAULT_ACCOUNT=918221680168 CDK_DEFAULT_REGION=us-east-1
cd /mnt/c/Dev/xc_data_analysis

# Keep Ask the Data off until step 5 verifies it.
aws ssm put-parameter --name /xc-platform/XC_AGENT_ENABLED --type String --value false --overwrite
```

## 1. Agent stack, phase 1

This creates the ECR repo, Memory, the Python sandbox and the IAM role. It
does not create the runtime yet.

```bash
cd infrastructure/cdk
source .venv-wsl/bin/activate
npx aws-cdk@2.1032.0 deploy XcPlatform-Agent -c deployAgentRuntime=false
cd ../..
```

## 2. Build and push the agent image (arm64)

AgentCore runs arm64 only. On an x86 machine, Docker needs QEMU (Docker Desktop
includes it). On plain Docker Engine in WSL, run this once first:
`docker run --privileged --rm tonistiigi/binfmt --install arm64`.

```bash
AGENT_REPO=$(aws cloudformation describe-stacks --stack-name XcPlatform-Agent \
  --query "Stacks[0].Outputs[?OutputKey=='AgentRepositoryUri'].OutputValue" --output text)
aws ecr get-login-password | docker login --username AWS --password-stdin "${AGENT_REPO%%/*}"
docker buildx build --platform linux/arm64 -f Dockerfile.agent -t "$AGENT_REPO:latest" --push .
```

## 3. Agent stack, phase 2

This creates the runtime.

```bash
cd infrastructure/cdk
npx aws-cdk@2.1032.0 deploy XcPlatform-Agent
cd ../..
```

## 4. API image, frontend, and web stack

```bash
# API image (x86, same repo and process as before)
API_REPO=918221680168.dkr.ecr.us-east-1.amazonaws.com/xcplatform-web-apirepositoryb8378b43-9t0qjs0x7i8c
aws ecr get-login-password | docker login --username AWS --password-stdin "${API_REPO%%/*}"
docker build -t "$API_REPO:latest" . && docker push "$API_REPO:latest"

# Frontend static export (Node 20.19.1)
cd web
NEXT_PUBLIC_AUTH_MODE=cognito NEXT_PUBLIC_API_BASE_URL=https://yrwinjfgp2.us-east-1.awsapprunner.com pnpm build
cd ..

# Web stack: adds the agent-users group, XC_AGENT_RUNTIME_ARN, and the
# InvokeAgentRuntime permission, and publishes web/out.
cd infrastructure/cdk
XC_WEB_SOURCE_DIR=/mnt/c/Dev/xc_data_analysis/web/out npx aws-cdk@2.1032.0 deploy XcPlatform-Web
cd ../..

# App Runner has not reliably picked up env-only changes; redeploy explicitly.
aws apprunner start-deployment --service-arn \
  arn:aws:apprunner:us-east-1:918221680168:service/ApiService-6UEoRTkXfkzx/211a2d2130af4b76915bed728ad72fe1
```

When the new API container starts, it now loads the published database into
its writer (the startup hydration from Task 19.0). Check its log for a clean
start.

## 5. Verify, then turn Ask the Data on

```bash
curl -s https://yrwinjfgp2.us-east-1.awsapprunner.com/api/v1/system/health
aws ssm put-parameter --name /xc-platform/XC_AGENT_ENABLED --type String --value true --overwrite
```

The switch takes effect within 30 seconds, with no redeploy. Then, in the
browser:

1. Sign in.
2. Open **Ask**.
3. Ask "Who leads the 2025 Saint Sebastian standings in each division?"
4. Watch the answer stream in.

If the answer arrives all at once after a pause instead of streaming, App
Runner is buffering the SSE response. Tell Claude; the fix is on the proxy or
header side, not in the agent.

## 6. Who may use Ask the Data

Admins always have access. To grant anyone else:

```bash
aws cognito-idp admin-add-user-to-group --user-pool-id us-east-1_rAlNsiZSQ \
  --username <their-email> --group-name agent-users
```

The change takes effect at their next sign-in. To remove access, run
`admin-remove-user-from-group` with the same arguments. To turn the feature
off for everyone, set `XC_AGENT_ENABLED` to `false` (step 0).

## 7. Alarms and budgets

This creates the SNS topic, the App Runner 5xx and latency alarms, and
monthly budgets: Bedrock $25 and the whole account $75. Budgets alert at 80%
of actual spend and at 100% of forecast.

```bash
cd infrastructure/cdk
npx aws-cdk@2.1032.0 deploy XcPlatform-Ops -c alertEmail=<your-address>
cd ../..
```

Confirm the subscription from the email AWS sends.

## 8. Load 2026 Developmental Meet 1

1. Go to **Admin** → paste `https://runsignup.com/Race/Results/154050` → **Fetch results**.
2. Review the identity questions, schools first.
   - A local rehearsal of this exact meet found 9 school spellings to settle,
     including:
     - "BSM" is Basilica of St Mary.
     - "St. Francis of Assisi (Triangle)" is St Francis.
     - "Queen of Apostles" and "St. John the Beloved" are new parishes.
   - About 57 athlete questions came up in total (35 at first, 22 more once
     the schools were settled), all of them siblings. **Create all as new
     athletes** settles those.
3. Click **Publish results**. The rehearsal published 581 results.

## 9. Owner data correction: St. John the Beloved

The school recorded since 2023 as "St John the Evangelist" is St. John the
Beloved (McLean, VA; Diocese of Arlington). To correct it after deploying:

1. Open **Schools** → **St John the Evangelist** → **Rename**.
2. Set the name to `St John the Beloved`.
3. Give a reason, for example "Correct parish name: McLean, VA (Diocese of
   Arlington)".
4. Click **Rename and publish**.

All results from 2023 onward, the rosters and past spellings stay with the
school. The change is recorded in the decision history with your reason.
Do this before step 8, so the 2026 intake's "St. John the Beloved" matches
the school by name.

## Rollback

- **API or frontend:** push the previous image tag and redeploy, or redeploy
  the previous `web/out`.
- **Ask the Data:** set `XC_AGENT_ENABLED=false`. Destroying the Agent stack
  is not needed.
- **Data:** publications are immutable snapshots, and restore goes through
  `SnapshotPublisher.restore` (Task 6.4).
