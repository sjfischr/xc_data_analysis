# Protected API delivery evidence (Task 3.7, gate F10 — completed)

**Deployed, tested, and torn down:** September 21, 2026, `us-east-1`, account
`918221680168`. Disposable resources only: 1 Cognito user pool, 1 Lambda
function, 1 IAM role, 1 API Gateway HTTP API. All confirmed absent after
teardown via `list-user-pools`, `list-functions`, `get-apis`, `list-roles`.

This supersedes the Cognito-only auth work recorded in
[auth-delivery-evidence.md](auth-delivery-evidence.md) (invite-only sign-in,
role claims, connection tickets — still valid) by adding the piece that was
previously missing: **an actual protected HTTP entry point**, tested with real
requests and real tokens.

Handler source: [protected-api-prototype-handler.py](../feasibility/protected-api-prototype-handler.py).

## What was built

- **Cognito**: invite-only user pool, `admin`/`viewer` groups, app client (no
  secret), two real users with real issued tokens (same proven pattern as the
  earlier F10 probe).
- **API Gateway HTTP API** with a native **JWT authorizer** — `Issuer` and
  `Audience` set to the Cognito pool and app client, so API Gateway itself
  verifies signature, issuer, audience, and expiry **before** any application
  code runs.
- **One Lambda function**, least-privilege role (`AWSLambdaBasicExecutionRole`
  only — CloudWatch Logs, nothing else), three routes:
  - `GET /health` — **no authorizer attached**, proves a public route can
    coexist with protected ones without leaking anything.
  - `GET /protected/data` — authorizer required; any verified identity may
    read.
  - `POST /admin/action` — authorizer required, **plus a server-side role
    check in the handler** for `admin` group membership. This is the concrete
    proof of design §6.2's rule that hiding a control client-side is never
    authorization — the check happens after the token is already verified,
    inside application code, on every call.

## Test matrix — 8 of 9 exactly as expected, 1 informational, 1 significant

| # | Caller | Route | Expected | Got | Result |
|---|---|---|---|---|---|
| 1 | anonymous | `GET /health` | 200 | 200 `{"authenticated": false}` | **PASS** |
| 2 | anonymous | `GET /protected/data` | 401 | 401 | **PASS** |
| 3 | anonymous | `POST /admin/action` | 401 | 401 | **PASS** |
| 4 | viewer | `GET /protected/data` | 200 | 200, `"role": ["viewer"]` | **PASS** |
| 5 | viewer | `POST /admin/action` | 403 | 403 `"admin role required"` | **PASS** |
| 6 | admin | `POST /admin/action` | 200 | 200 | **PASS** |
| 7 | admin | `GET /protected/data` | 200 | 200, `"role": ["admin"]` | **PASS** |
| 8 | garbage token | `GET /protected/data` | 401 | 401 | **PASS** |
| 9 | valid token, no `Bearer ` prefix | `GET /protected/data` | — | 200 | **informational** |
| 10 | revoked (but unexpired) token | `GET /protected/data` | 401 (expected) | **200 — still accepted** | **FINDING** |

Every response body and status code was captured directly from `curl` against
the live endpoint, not simulated.

## Anonymous access is fully blocked — R13.4, confirmed live

Checks 1–3 directly answer Task 3.7's third bullet: an unauthenticated caller
gets `401` on every data or admin route, and the public route returns nothing
sensitive. Role separation (checks 4–7) confirms `admin`/`viewer` claims are
usable for real authorization decisions, not just present in the token.

## Informational: the JWT authorizer accepts a bare token

Check 9 sent `Authorization: <token>` with no `Bearer ` prefix, and it was
accepted. This is not a bypass — the token itself was still fully verified
(signature, issuer, audience, expiry); API Gateway's identity-source extraction
is simply tolerant of the prefix. Worth a consistent convention in the real
client (Task 14.1), not a security gap.

## The significant finding: a stateless JWT authorizer does not see revocation

Calling `admin-user-global-sign-out` against the Cognito user pool, then
retrying with the **same, still-unexpired** viewer access token:

- **Cognito itself**, asked directly (`GetUser` with that token): *"Access
  Token has been revoked."*
- **API Gateway's JWT authorizer**, asked with the identical token: **`200`,
  full protected response.**

This is not a misconfiguration — it is how a stateless JWT authorizer works by
design. It checks the token's own claims (signature, `iss`, `aud`, `exp`)
against the issuer's public keys and stops there. It never calls back to
Cognito to ask whether the token has since been revoked. **A revoked user's
token remains fully valid against this kind of protected API until its natural
expiry** — up to the full access-token TTL (3,600 s / 1 hour as configured
here).

**Consequence for the design.** This is direct, live confirmation of why
design §6.2 already specifies a **backend-for-frontend (BFF) cookie session**
as the primary approach rather than a bare bearer-JWT API: a BFF holds its own
server-side session record, so revoking a user takes effect on the *next
request*, not at token expiry. A bare `Authorization: Bearer <JWT>` pattern —
which is what this prototype deliberately tested, to measure the alternative —
cannot deliver R13's "revocation" acceptance criterion by itself. Options for
Task 13.4, in order of preference:

1. **BFF/cookie session (the design's existing choice)** — the API reads a
   session identifier from a Secure/HttpOnly/SameSite cookie and checks it
   against a server-side session store on every request; revoking the session
   record takes effect immediately.
2. If a bearer-JWT pattern is used anywhere (e.g. a service-to-service call),
   keep token TTL short (minutes, not an hour) to bound the exposure window,
   and treat "revoked eventually, not immediately" as an accepted, documented
   limitation — never as equivalent to (1).

## Lambda@Edge comparison

No new infrastructure was built for this — analysis only, as in
[auth-delivery-evidence.md](auth-delivery-evidence.md). Nothing measured here
changes that comparison's conclusion (keep the CloudFront + API Gateway + Lambda
BFF as primary; Lambda@Edge remains the fallback if entry-point latency proves
unacceptable in Task 16.4). The revocation finding above is a property of the
*authorization mechanism* (bearer JWT vs. server-side session), not of the
*edge routing choice* (CloudFront origin vs. Lambda@Edge) — it applies equally
to either.

## What Task 3.7 still does not include

- A deployed CloudFront distribution and protected static HTML origin — this
  probe tested the API layer only, which is where the security-relevant
  decisions (anonymous rejection, role enforcement, revocation) live. Wiring
  CloudFront in front of it is routing and caching, not a new mechanism, and
  remains Task 15.2 scope.
- Real-account rollout mechanics (invite email flow, password reset).
- Measured latency/cost at the edge — cost is no longer a project requirement,
  and latency measurement belongs to Task 16.4 against the real deployment.
