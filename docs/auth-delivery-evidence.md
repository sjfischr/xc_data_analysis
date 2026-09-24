# Protected delivery evidence (Task 3.7, gate F10) — PARTIAL

**Observed:** September 20, 2026, `us-east-1`, account `918221680168`.
**Status: F10 is partially verified.** Cognito invite-only sign-in, role claims,
and the WebSocket connection-ticket scheme are proven. The protected
CloudFront/API Gateway entry point and the deployed cost measurement are **not**.

All Cognito resources created for this probe were deleted; a follow-up
`list-user-pools` confirmed none remain.

## What was proven

### Invite-only sign-in

A user pool created with `AllowAdminCreateUserOnly=true` refuses self
registration outright:

> `NotAuthorizedException` — SignUp is not permitted for this user pool

Administrator-created users authenticate normally. Wrong passwords are rejected
with `NotAuthorizedException`, and an API call with bogus credentials is
rejected before reaching any data.

### Administrator / viewer claims

Two users were created, one per group, and their real tokens decoded:

| Check | Result |
|---|---|
| `cognito:groups` in the **ID token** | present (`["admin"]` / `["viewer"]`) |
| `cognito:groups` in the **access token** | present |
| `sub` present | yes |
| Token lifetime | 3,600 s |
| Refresh token issued | yes |
| **`email` present in token** | **no** |

Two consequences for the design:

1. Because `cognito:groups` appears in the **access token**, the API can make
   server-side role decisions from the token the client presents, without an
   extra directory lookup per request.
2. Because no email is present, the pseudonymous `actor_id` HMAC (design §12.4)
   can be derived from `sub` with no PII in the memory namespace — the data
   minimization requirement is satisfiable as specified.

### WebSocket connection tickets

Browsers cannot set custom headers on a WebSocket handshake, so the tempting
shortcut is a token in the query string — which leaks it into CloudFront logs,
API Gateway access logs, and browser history.

`src/xc_platform/feasibility/connection_ticket.py` implements the alternative:
the authenticated HTTP API mints a short-lived, single-use, HMAC-signed ticket
bound to session, actor, and role, passed as a WebSocket subprotocol. Fourteen
security tests cover it:

| Property | Test result |
|---|---|
| Valid ticket round-trips | pass |
| Replay rejected (single use) | pass |
| Expired ticket rejected | pass |
| Ticket signed with another key rejected | pass |
| Tampered payload (viewer→admin) rejected | pass |
| Ticket replayed on another session rejected | pass |
| Malformed tickets rejected (5 variants) | pass |
| Unsupported role cannot be issued | pass |
| Actor id is pseudonymous, stable, key-dependent | pass |

**Carried design consequence:** replay protection uses an in-process set in the
prototype. In production it must be a short-TTL shared store (DynamoDB or
ElastiCache), or replay protection silently fails across Lambda instances. This
is a real requirement for Task 13.5, not an implementation detail.

## What was NOT proven

| Task 3.7 bullet | Status |
|---|---|
| Cognito invite-only sign-in | **Proven** |
| Administrator/viewer claims | **Proven** |
| WebSocket connection-ticket validation | **Proven (logic; not deployed)** |
| Protected HTML/API access | **Not built** — no CloudFront distribution, API Gateway, or Lambda was deployed |
| Compare selected approach with the Lambda@Edge alternative | **Analysis only** (below), no measurement |
| Anonymous users cannot access race data, agent calls, admin operations | **Partial** — proven at the Cognito layer; no deployed endpoint was tested |
| Measure baseline and burst cost across the service set | **Not measured** — nothing is deployed to measure, and current model pricing is not machine-retrievable |

Deploying a CloudFront distribution, HTTP API, WebSocket API, and Lambda
origin is a substantial build that belongs to Task 15.2. What Task 3.7 needed to
de-risk — that invite-only auth carries usable role claims without PII, and that
WebSocket auth need not leak tokens — is settled.

## Protected-entry-point comparison (analysis, not measurement)

| Approach | Pros | Cons |
|---|---|---|
| **CloudFront + API Gateway HTTP API + Lambda BFF** (design §6.2) | No always-on compute; cookies set by the BFF; one origin; simplest IAM | Protected HTML must be served by Lambda, so entry-point requests pay a cold-start |
| **Lambda@Edge authentication** | Auth at the edge, static HTML stays on S3 and cacheable | Lambda@Edge is us-east-1-only for deployment, has lower limits, slower deploys, and no environment variables; harder to debug |

The measured evidence does not favor changing the design's primary choice.
Hashed static assets carry no race data and can stay publicly cacheable; only
HTML entry points and APIs need a session. **Recommendation: keep the design's
CloudFront + API Gateway + Lambda BFF, and treat Lambda@Edge as the fallback**
if entry-point latency proves unacceptable in Task 16.4.

## Cost drivers (enumerated, not measured)

At this workload — a seasonal, invite-only site with a handful of users — the
services in the design fall into three groups:

- **Effectively free at this scale:** Cognito (well under the monthly active
  user free allowance for a handful of invited users), SQS FIFO (a few messages
  per import), S3 storage (a 1.3 MiB database plus small raw payloads).
- **Small but non-zero and fixed-ish:** CloudFront requests and data transfer,
  CloudWatch logs (retention is the lever), S3 requests.
- **The real variable risk:** Bedrock tokens, AgentCore runtime time, and Tavily
  credits — the three the design already gates with quotas.

This ordering is consistent with design §16.3's budget policy. It is **not** a
measurement and must not be presented as one. A real baseline/burst figure
requires the staging deployment in Task 16.5.
