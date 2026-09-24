---
name: xc-agentcore-memory-identity
description: 'Use when configuring AgentCore memory or identity for the XC platform: actor_id/session scoping, youth-data memory limits, Cognito invite-only groups, and least-privilege IAM role separation.'
---

# AgentCore memory scoping and identity

> Status: **design policy** (normative for this project), and the identity
> derivation rule below is **confirmed by a live deployment** (Task 3.5,
> 2026-09-21) -- not just illustrative.
> Docs checked 2026-09-20:
> [AgentCore Memory](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/memory.html),
> [AgentCore Identity](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/identity.html).

## Memory scoping (youth-data safeguards)

| Setting | Release-1 policy |
|---------|------------------|
| `actor_id` | `HMAC(Cognito subject)` — never the raw subject, email, or name |
| `session_id` | fresh UUID per conversation |
| Short-term memory | enabled (conversation continuity) |
| Long-term memory | **opt-in only**, per user |
| Semantic long-term facts about athletes | **DISABLED in release 1** |

Rationale: this platform analyzes minors' race results. Authoritative facts
(aliases, merges, corrections) belong in the versioned SQLite database with
provenance — never in agent memory. Agent memory may hold user preferences
and conversational context only.

## Identity

- **Cognito, invite-only**: `admin` and `viewer` groups; no self-service
  signup.
- The API validates JWTs and passes only the derived actor ID and role to the
  agent; tools re-check authorization server-side (defense in depth).
- **Separate IAM roles** for: API service, ingestion jobs, publication
  writer, and agent runtime. The agent runtime role gets read-only access to
  published snapshots and no access to raw-source buckets or the manifest
  swap.

## Resolution agent: no memory at all

The entity-resolution agent is stateless: one bounded JSON case packet in,
one JSON recommendation out. It must not read or write AgentCore memory;
resolution history lives in `resolution_cases` / `resolution_decisions`
tables with full provenance.

## Confirmed live: there is no `user_id` on the AgentCore request context

`bedrock_agentcore.runtime.context.RequestContext` (SDK 1.23.1) has exactly
three fields: `session_id`, `request_headers`, `request`. **There is no
`user_id` field**, and `agentcore invoke --user-id <x>` is a CLI-local label
that is never delivered to the entrypoint. Code of the shape
`getattr(context, 'user_id', 'default-user')` silently returns the default for
*every* caller, collapsing all actors onto one identity -- reproduced live: two
different `--user-id` values on the same session id shared conversation memory.

The only per-request identity channel the SDK exposes is the inbound
`Authorization` header, forwarded verbatim into `context.request_headers`. The
correct pattern:

1. Configure the runtime's `authorizerType` as `CUSTOM_JWT` against the
   Cognito user pool so AgentCore verifies the token before the entrypoint
   runs (the default `AWS_IAM` authorizer carries no per-user claims).
2. Read the verified token from `context.request_headers["authorization"]`.
3. Derive `actor_id = HMAC(sub, application_key)` -- never the raw `sub`,
   and never anything the caller supplies unauthenticated (a CLI flag, a
   request body field, a query parameter).

See [agentcore-deployment-evidence.md](../../../docs/agentcore-deployment-evidence.md)
for the full trace of this finding, including that AgentCore Memory's own
`list_events` correctly filters by `actor_id` -- the leak is only ever in how
`actor_id` gets derived, never in the memory service itself.

## Anti-patterns

- Storing athlete facts, guesses, or merge decisions in long-term memory.
- Using email addresses or names as `actor_id`.
- One shared IAM role "to simplify deployment".
- Trusting a model-supplied user ID or role claim.
- **Reading identity from `context.user_id` or any CLI-supplied user/session
  flag** — confirmed live to not exist; always derive from the verified
  `Authorization` header via a `CUSTOM_JWT` authorizer.
