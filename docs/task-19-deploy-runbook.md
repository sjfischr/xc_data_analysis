# Task 19 deploy runbook: new site, Ask the Data, intake, alarms

The owner runs these from WSL in `/mnt/c/Dev/xc_data_analysis`, with the usual
AWS profile. Account 918221680168, region us-east-1.

Order matters. The agent runtime needs an image before it can exist, and the
web stack reads the runtime's ARN.

> **Always deploy one stack at a time with `--exclusively`** (2026-10-18).
> Ops depends on Web, and Web depends on Agent, so `cdk deploy XcPlatform-Web`
> without it also redeploys the Agent stack. With no `-c agentImageTag`, that
> pointed the live agent at the Sept 24 image, which refused the schema-5
> data and failed every chat. Agent image changes go only through
> `scripts/redeploy_agent_v2.sh`. The agent repository no longer has a
> `latest` tag, so a deploy without a tag fails instead of rolling back.

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
XC_WEB_SOURCE_DIR=/mnt/c/Dev/xc_data_analysis/web/out npx aws-cdk@2.1032.0 deploy --exclusively XcPlatform-Web
cd ../..

# App Runner has not reliably picked up env-only changes; redeploy explicitly.
aws apprunner start-deployment --service-arn \
  arn:aws:apprunner:us-east-1:918221680168:service/ApiService-6UEoRTkXfkzx/211a2d2130af4b76915bed728ad72fe1
```

The Web stack deploy refuses a `web/out` built for local development: any
`http://localhost` in the built JavaScript stops it before anything is
published. If it stops, rebuild the frontend with the two
`NEXT_PUBLIC_*` values above, using Node 20.19.1 (`nvm use 20.19.1`).

When the new API container starts, it now loads the published database into
its writer (the startup hydration from Task 19.0). Check its log for a clean
start.

## 5. Verify, then turn Ask the Data on

### Background chat jobs (2026-11-07)

The production browser now uses `POST /api/v1/chat/jobs` followed by short
`GET /api/v1/chat/jobs/{job_id}?after=<cursor>` requests. Refresh replays
durable events and reconnects to the same job; it does not rerun the model.
The job ID is the submission idempotency key. Retrying that ID is safe even
if the start acknowledgement was lost. Only one job runs per conversation.
Completed answers are in AgentCore Memory as before; complete charts, code,
and progress are in the private job bucket, not semantic athlete memory.

`XcPlatform-Agent` owns a separate encrypted, unversioned `ChatJobBucket`
with 30-day expiry. Both Agent and Web stacks pass `XC_CHAT_JOB_BUCKET`;
their IAM write grants target only its `chat-jobs/*` prefix. Unversioned
storage makes history deletion actually remove the job objects, unlike
the immutable, versioned database bucket. Neither job storage nor the
agent can modify published database snapshots.

Deployment order for this change:

1. Build/push a uniquely tagged ARM64 agent image and deploy **only**
   `XcPlatform-Agent` with `-c agentImageTag=<tag>`. Confirm V2 and READY,
   using `scripts/redeploy_agent_v2.sh` as for other agent changes.
2. Build/push the API image and build the production frontend (step 4).
   Deploy **only** `XcPlatform-Web`, then explicitly restart App Runner.
3. Verify a job submission acknowledges quickly, disconnect/reload, and
   confirm the same job's final answer is replayed. Test **Stop**, a
   repeated submission ID, actor isolation, and **Delete my history**.
   The old production `/chat/messages` synchronous endpoint now returns
   an explanatory conflict instead of starting unobservable long work.

Each job gets a new AgentCore runtime session, so an old conversation's
instance lifetime does not shorten new work. The runtime registers an SDK
async task and keeps health busy until cleanup completes. The wall-clock
budget is 28,740 seconds (eight hours minus a persistence buffer), with
existing token/iteration caps unchanged. A five-second heartbeat makes a
worker loss explicit after 90 seconds rather than leaving a forever-busy
question. Stop writes an actor-scoped cancellation marker; leaving the
page only stops watching. There is no automatic model escalation or
automatic restart after a terminal failure.

Time, token, and iteration stops have distinct UI labels. Interrupted
usage is marked partial, with unknown token counters represented as null,
not invented zeros. The data publication is pinned once per running turn.
Saved jobs are capped at 50 per conversation and 200 conversations per
actor; reaching a cap asks for a new conversation or history deletion.
History deletion holds the actor operation lease through both job removal
and Memory deletion, renewing it during long object cleanup so new questions
cannot race the erasure.

Rollout evidence (2026-11-07):

- Agent stack and Web stack updates succeeded. Runtime version 12 is READY
  on V2 with `maxLifetime=28800`, image tag `background-jobs-20261107`,
  and the separate bucket
  `xcplatform-agent-chatjobbucketb1372146-t2plkcwhdy1g`.
- Final API image digest:
  `sha256:1399cd32639482d98482ebb0e1ed5d68675fc5c5e89efd642c07f97fd9d76e55`.
  Web's App Runner environment update operation was
  `683fc02c6a6149f880bbb63798429ddd`; explicit final image deployment
  `43ccbc60432e43d3b8b8c31fdd921072` also succeeded. The live API health
  check passed and its schema exposes all three job endpoint paths with
  development login absent.
- A live isolated aggregate-only analysis acknowledged in 4.48 seconds,
  then completed in 184.10 seconds after the invocation response had
  closed. All 114 saved events, including the final answer, replayed
  identically across pagination. Repeated submission of its UUID left
  the terminal record unchanged and did not rerun the model.
- A separate running job was cancelled successfully. Cross-actor access
  returned not found. All isolated probe jobs and their Memory events were
  erased; the job bucket had zero remaining objects afterward.
- Authenticated production browser checks returned HTTP 202 on submission.
  Refresh during execution reconnected to the same job with zero POST
  resubmissions. Completed-answer refresh restored its prose, aggregate
  table, chart, and actual four-cycle footer. **Stop** returned HTTP 200
  and the job became cancelled; another refresh preserved that status and
  the earlier chart. Browser chat persistence contained only a UUID.
  Existing user history was not deleted.
- The job bucket is private, SSE-S3 encrypted, unversioned, and expires
  `chat-jobs/` objects after 30 days. The repaired data publication remained
  `b31b03fa-55f2-450e-a738-18267b71582e`.
- Offline validation: 230 affected backend tests, 18 infrastructure tests,
  and 17 frontend chat tests passed; Ruff, full Python mypy, frontend
  TypeScript checking, and the production static build passed.
- Browser testing also exposed a composer-remount race during focus-time
  account verification. The frontend now conceals unverified content
  without detaching the composer; its draft survives verification while
  actual account changes still clear prior content. A regression test
  covers draft, history/chart preservation, and account isolation.
  The final frontend-only rollout succeeded; a live focus/recheck test
  preserved the textarea node, first-fill draft, enabled Ask button, and
  earlier chart across two authentication checks.

This verifies execution beyond the former 100/120-second deadlines, not an
eight-hour endurance run. Existing token and iteration caps can still stop
a question sooner. An independently submitted paraphrase is not a semantic
answer-cache hit; durable replay and same-ID retries avoid recomputation.

```bash
curl -s https://yrwinjfgp2.us-east-1.awsapprunner.com/api/v1/system/health
aws ssm put-parameter --name /xc-platform/XC_AGENT_ENABLED --type String --value true --overwrite
```

The switch takes effect within 30 seconds, with no redeploy. Then, in the
browser:

1. Sign in.
2. Open **Ask**.
3. Ask "Who leads the 2025 Saint Sebastian standings in each division?"
4. Watch progress arrive through polling; refresh while it runs and confirm
   it reconnects to the same question instead of submitting another one.
5. Verify **Stop** and history deletion. The API restart requires signing in
   again because its login sessions are in memory.

Events are coalesced before persistence, so short batches rather than
token-by-token updates are expected. Polling failures should show an
explicit reconnecting message, preserving saved progress and the cursor.

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
npx aws-cdk@2.1032.0 deploy --exclusively XcPlatform-Ops -c alertEmail=<your-address>
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

## 10. 2023 Meet 2 and race-day weather (2026-10-18)

Owner decisions are recorded in requirements.md R1.9–R1.11 and R4.9. Order
matters: migration 0004 adds the weather tables, and a reader refuses a
snapshot newer than its code.

1. **Deploy the code first.** Push the API image and restart it (step 4),
   and redeploy the agent (`scripts/redeploy_agent_v2.sh`). Both must know
   migration 0004 before any snapshot that uses it is published.
2. **Load the weather** for every meet already in the database, archiving
   the raw responses:

   ```bash
   BUCKET=$(aws cloudformation describe-stacks --stack-name XcPlatform-Storage      --query "Stacks[0].Outputs[?starts_with(OutputKey,'ExportsOutputRefDataBucket')].OutputValue"      --output text)
   python scripts/load_race_weather.py /tmp/wx "$BUCKET" --upload-raw
   python scripts/publish_snapshot.py /tmp/wx/xc.db "$BUCKET"        # dry run
   python scripts/publish_snapshot.py /tmp/wx/xc.db "$BUCKET" --yes
   aws apprunner start-deployment --service-arn <api-service-arn>    # reload
   ```

   The dry run must show results +0 / −0; this load changes only dates,
   venues, race start times, and weather.
3. **Import 2023 Meet 2** in Admin intake with
   `https://runsignup.com/Race/Results/154767`. Only that race's 2023 events
   are importable; review the matches and publish as usual. The 2023 Saint
   Sebastian standings then cover all three meets.

   This race's REST results carry no team, so the intake reads each
   runner's Group/Team from the results page and joins it by bib and name
   (2026-10-18 fix). That needs the API, agent, and website from that fix,
   with migration 0005, deployed **before** the import: the API publishes
   the import at schema 5, and an agent that predates 0005 would refuse it.
   Expect all 471 rows ready, 7 school questions (each offering the right
   school first, including OLOH and Q of A), and about 13 athlete
   questions. Some 2023 registrants entered their names last-first
   ("Peterson Caroline"), so check those against the existing athletes.
   An earlier run of this URL that opened 471 "school (no name)" questions
   can be ignored; it cannot publish.
4. **Load its weather** by repeating step 2 in a new work directory. The
   loader skips 2023 Meet 2 until the meet exists, and re-running is safe.

## Routine update (after the first deploy)

Run these from WSL. First run `export AWS_CA_BUNDLE=/mnt/c/Users/sjfis/.aws/ca-bundle-with-norton.pem`
and `nvm use 20.19.1`. Skip any part the change does not touch.

1. **Agent code** (anything under `src/xc_platform/agents` or `analytics`):
   push a uniquely tagged image, then point the runtime at it. The runtime
   only takes a new image when its tag changes; re-pushing `:latest` does
   nothing.

   `scripts/redeploy_agent_v2.sh` does all of this and also keeps the
   runtime on AgentCore platform V2 (design.md 12.6), which CDK can't set.
   Nothing needs activating. It runs CDK on `infrastructure/cdk/.venv-wsl`,
   and on first use it builds `.venv-deploy` with the pinned boto3 that the
   V2 step needs. In WSL, run `git config core.autocrlf true` once, or a
   tree that is clean on Windows looks dirty.

   ```bash
   scripts/redeploy_agent_v2.sh --preflight-only      # checks only, no build
   scripts/redeploy_agent_v2.sh                       # current defaults
   scripts/redeploy_agent_v2.sh --model us.anthropic.claude-sonnet-4-6 --effort medium
   ```

   It refuses a dirty tree (pass `--allow-dirty` to override), tags the
   image with the commit hash, and ends by checking that the runtime is
   READY, on V2, and on the new tag. The manual steps it replaces:

   ```bash
   TAG=$(git rev-parse --short HEAD)
   AGENT_REPO=$(aws cloudformation describe-stacks --stack-name XcPlatform-Agent \
     --query "Stacks[0].Outputs[?OutputKey=='AgentRepositoryUri'].OutputValue" --output text)
   aws ecr get-login-password | docker login --username AWS --password-stdin "${AGENT_REPO%%/*}"
   docker buildx build --platform linux/arm64 -f Dockerfile.agent -t "$AGENT_REPO:$TAG" --push .
   cd infrastructure/cdk && npx aws-cdk@2.1032.0 deploy --exclusively XcPlatform-Agent -c agentImageTag=$TAG && cd ../..
   ```

2. **API**: step 4's API image build and push.
3. **Frontend**: step 4's `pnpm build` and the `XcPlatform-Web` deploy.
4. **Restart the API last**, with step 4's `aws apprunner start-deployment`.
   It reloads the newest published data at startup.

Data corrections done outside the site, such as `scripts/merge_schools.py`
followed by `scripts/publish_snapshot.py`, go before step 4.

## Rollback

- **API or frontend:** push the previous image tag and redeploy, or redeploy
  the previous `web/out`.
- **Ask the Data:** set `XC_AGENT_ENABLED=false`. Destroying the Agent stack
  is not needed.
- **Data:** publications are immutable snapshots, and restore goes through
  `SnapshotPublisher.restore` (Task 6.4).
