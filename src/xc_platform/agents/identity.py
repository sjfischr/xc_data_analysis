"""Actor identity derivation for AgentCore Memory (Task 11.5/11.6,
design.md section 12.4, Requirement 11.6).

The Task 3.5 feasibility prototype's entrypoint
(``feasibility/agentcore/xcfeasibility/app/xcanalytics/main.py``) documents
two load-bearing findings this module exists to fix:

1. ``bedrock_agentcore.runtime.context.RequestContext`` has exactly three
   fields -- ``session_id``, ``request_headers``, ``request`` -- and no
   ``user_id``. A CLI-local ``--user-id`` flag is never delivered to the
   entrypoint. Confirmed live: two different ``--user-id`` values against
   the same session both resolved to the same cached agent and the same
   AgentCore Memory actor, so the second caller read the first caller's
   conversation.
2. Production identity must come from the inbound ``Authorization`` header
   AgentCore forwards verbatim (via a ``CUSTOM_JWT`` authorizer configured
   on the runtime, so AgentCore itself verifies the token before this code
   runs), and the actor ID must be an HMAC of the token's ``sub`` claim
   (design.md 12.4) -- never the raw subject, email, or name, which would
   put personally identifying information directly in AgentCore Memory
   namespacing.
"""

from __future__ import annotations

import hashlib
import hmac

ACTOR_ID_UNAUTHENTICATED = "unauthenticated"


def derive_actor_id(subject: str, application_key: bytes) -> str:
    """A deterministic, non-reversible actor ID for one Cognito subject.

    Deterministic so the same user's AgentCore Memory is found again in a
    later session; an HMAC (not a bare hash) so it cannot be recomputed
    without ``application_key``, and a raw ``sub`` value logged or leaked
    elsewhere cannot be correlated back to it.
    """
    if not subject:
        raise ValueError("subject must be non-empty")
    digest = hmac.new(application_key, subject.encode("utf-8"), hashlib.sha256)
    return digest.hexdigest()
