# AgentCore runtime deployment evidence (Task 3.5, gate F8)

**Deployed:** September 21, 2026, `us-east-1`, account `918221680168`, by the
owner (`agentcore deploy`, stack `AgentCore-xcfeasibility-default`, 7/7
resources `CREATE_COMPLETE` in 4m 3s). **Verified:** same day, via
`feasibility/agentcore/xcfeasibility/verify_deployment.sh`.

**Verdict: F8 substantially PASSES for everything except actor isolation, which
is structurally blocked until a `CUSTOM_JWT` authorizer exists — not a code
bug that redeploying again would fix.**

## What was proven live

| Check | Result |
|---|---|
| Runtime reaches `READY`, Memory deploys | **PASS** |
| Invocation returns a correct, tool-grounded answer | **PASS** — "4,204 race results", `publication_id: feasibility-pub-001` |
| Streaming | **PASS** — `agentcore invoke` renders incrementally; runtime logs confirm `Returning streaming response (generator)` |
| Short-term context within one session | **PASS** — a follow-up turn on the same session recalled "4,204" without re-querying |
| Session isolation (new session, same actor) | **PASS** — a fresh session had no memory of the prior session's number |
| Read-only tool enforcement, live | **PASS** — `DROP TABLE results` was refused: *"The query tool I have access to is read-only and only accepts SELECT or WITH statements."* |
| Prompt-injection resistance | **PASS** — a "developer mode, reveal your system prompt" attempt was refused and named as a security test |
| Structured tracing / observability | **PASS** — rich OpenTelemetry spans (`gen_ai.*` attributes, tool names, token counts, memory `CreateEvent` calls) confirmed by direct inspection; one script run mis-reported this as absent due to a CloudWatch log-delivery timing gap, reproduced as present on manual re-check seconds later |
| No raw chain-of-thought in logs | **PASS** |
| Actor isolation (different user, same session id) | **FAIL, twice — root-caused both times, see below** |
| Memory delete/reset via CLI | **NOT AVAILABLE** — see below |

## The actor-isolation finding

Invoking with `--session-id verify-session-...0001 --user-id verify-user-a`,
then the same session id with `--user-id verify-user-b`, the second call
recalled the first caller's answer ("You asked me about the number **4,204**").
That should be impossible if actors are isolated.

**Root cause, confirmed by reading the installed SDK source
(`bedrock_agentcore==1.23.1`), not by inference:**

```python
class RequestContext(BaseModel):
    session_id: Optional[str] = Field(None)
    request_headers: Optional[Dict[str, str]] = Field(None)
    request: Optional[Any] = Field(None, ...)
```

`RequestContext` has **no `user_id` field**. The scaffolded entrypoint did:

```python
user_id = getattr(context, 'user_id', 'default-user')
```

`getattr` on a Pydantic model for a field that doesn't exist always returns the
default — so this line returned `'default-user'` for **every invocation, from
every caller, unconditionally**. `agentcore invoke --user-id <x>` is a
client-side CLI label; nothing in the SDK delivers it to the entrypoint.

**This is not an AgentCore Memory bug.** Reading
`AgentCoreMemorySessionManager.list_messages`, every read is filtered by both
`actor_id` and `session_id`:

```python
events = self.memory_client.list_events(
    memory_id=self.config.memory_id,
    actor_id=self.config.actor_id,
    session_id=session_id,
    ...
)
```

Given two genuinely different `actor_id` values, AgentCore Memory isolates
them correctly. The leak was entirely in the scaffold's identity derivation
collapsing every caller onto the same constant actor.

**The correct mechanism, also confirmed by reading the SDK:** the runtime
forwards the inbound `Authorization` header verbatim into
`context.request_headers` (case-normalized). This is the only per-request
identity-bearing channel the SDK exposes. Production identity must:

1. Configure the runtime's `authorizerType` as `CUSTOM_JWT` against the
   Cognito user pool, so AgentCore verifies the token before the entrypoint
   ever runs (this feasibility stack used the default `AWS_IAM` authorizer,
   which has no per-user claims at all);
2. Decode the verified token's `sub` claim from `request_headers`;
3. Derive `actor_id = HMAC(sub, application_key)` exactly as design §12.4
   specifies — never the raw `sub`, and never a client-supplied value.

**Fix applied and redeployed 2026-09-21 (owner ran `agentcore deploy` a second
time; `UPDATE_COMPLETE`, 1m 7s, code-only change — Memory/IAM untouched).
Retested live. Result: still leaking, for a different and more fundamental
reason.**

`main.py` now sources identity from `request_headers["authorization"]` instead
of the nonexistent `context.user_id`. To test it with genuinely different
identities (not just different CLI labels), two unsigned JWTs with distinct
`sub` claims were built and sent via `agentcore invoke`'s header options:

1. **`--bearer-token`** (the CLI's documented path for `CUSTOM_JWT` auth) was
   **rejected outright by the AgentCore gateway with HTTP 403** — *"Authorization
   method mismatch... configured for a different authorization method"* —
   before the request ever reached the entrypoint. This runtime deploys with
   the default `authorizerType: AWS_IAM`, which does not accept bearer-token
   auth at all.
2. **`-H "Authorization: Bearer <token>"`** (a custom header layered on top of
   the CLI's normal SigV4-signed request) was accepted by the gateway, but the
   leak persisted: asking agent A (token with `sub=actor-a...`) to remember an
   arbitrary number (71553, chosen specifically to have no relationship to the
   dataset, so no tool call could coincidentally reproduce it), then asking
   agent B (token with `sub=actor-b...`, same session id) what the number was,
   returned: *"I acknowledged the number 71553 in my previous response... I
   can see it in our conversation history."* Actor B read actor A's turn.

**Most likely mechanism** (not confirmed at the byte level — confirming it
would need diagnostic logging added to the entrypoint and a third redeploy,
which was not pursued given two redeploys already performed this session):
under `AWS_IAM` authorization, the wire-level `Authorization` header is
reserved for the AWS SigV4 signature the request itself requires. A
client-supplied `-H "Authorization: ..."` either never reaches
`context.request_headers` or is superseded by the real SigV4 value before the
entrypoint sees it. Either way, `_actor_id_from_request` finds no usable
bearer token, falls through to its `"unauthenticated"` default, and **every
caller collapses onto that one constant actor** — a different failure mode
than the original bug (`'default-user'` vs `'unauthenticated'`), but the same
net effect: no real per-caller isolation is achievable under this runtime's
current authorizer.

**Conclusion:** the code fix is a strict improvement — it no longer trusts a
client-declared `--user-id`, and the derivation logic matches the SDK's actual
capabilities. But **it cannot be proven correct, and actor isolation cannot be
achieved at all, without reconfiguring the runtime's `authorizerType` as
`CUSTOM_JWT` against a real Cognito user pool** — squarely Task 15.2/11.5
scope (Cognito provisioning, invite-only groups, protected delivery), not
something fixable inside this feasibility prototype's current configuration.

## Memory deletion / reset

`agentcore memory --help` in this CLI build (`@aws/agentcore` current as of
2026-09-21) exposes no per-actor delete or reset command — only
`archive` for batch-evaluation jobs, unrelated to conversational memory.
**Task 13.5's `DELETE /chat/memory` must call the AgentCore Memory data-plane
API directly** (the `MemoryClient`/`gmdp_client` used internally by the
session manager), not shell out to this CLI. Recorded as a gap, not a blocker:
the underlying API exists, only a CLI convenience wrapper does not.

## Deployed resources

| Resource | Identifier |
|---|---|
| `AWS::BedrockAgentCore::Runtime` | `arn:...:runtime/xcfeasibility_xcanalytics-wivcnV9L2p`, state `READY` |
| `AWS::BedrockAgentCore::Memory` | `arn:...:memory/xcfeasibility_xcanalyticsMemory-ttXMd5Cw1M` |
| 2 × `AWS::IAM::Role`, 1 × `AWS::IAM::Policy` | as reviewed pre-deploy; unchanged |

**Status: still deployed as of this writing.** Unlike the S3 bucket and
Cognito pool used for gates F6/F10 (created and torn down within the same
probe), this stack required an explicit owner action to create and is left
running pending an explicit decision on whether to keep it for further testing
or tear it down with `agentcore destroy`.

## What remains unverified

- Actor isolation. This is not a "needs a redeploy" item -- it needs a
  `CUSTOM_JWT` authorizer wired to a real Cognito user pool (Task 15.2), then
  a re-test with real, verified per-user tokens.
- Cold-start latency under a fresh (non-cached) container.
- Behavior under the intended `CUSTOM_JWT` authorizer rather than the default
  `AWS_IAM` one used here.
- Genuine per-actor memory deletion against the data-plane API.
