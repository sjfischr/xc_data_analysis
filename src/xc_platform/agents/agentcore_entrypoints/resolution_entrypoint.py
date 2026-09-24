"""AgentCore Runtime entrypoint for the resolution agent (Task 11.5,
design.md sections 12.1, 12.6).

Deploy-time artifact -- see the package docstring for why this module is
not imported by the ordinary test suite.

Service-facing, not user-facing (design.md 12.1): this entrypoint is called
by the intake workflow (Task 12.3), not directly by an end user, and
carries no conversational memory -- every invocation is one bounded
candidate packet in, one schema-validated proposal out. There is
accordingly no per-user actor identity to derive here, unlike
:mod:`xc_platform.agents.agentcore_entrypoints.analytics_entrypoint`; the
runtime's authorizer instead authenticates the calling service (a
service-to-service credential, configured at deploy time), not a Cognito
end user.
"""

from __future__ import annotations

from bedrock_agentcore.runtime import BedrockAgentCoreApp

from xc_platform.agents.resolution_agent import (
    build_resolution_agent,
    propose_resolution,
)
from xc_platform.agents.resolution_contract import CandidatePacket
from xc_platform.resolution.evidence import Candidate

# --- Startup phase (module scope): the agent's model config is static for
# the life of this runtime version, so building it once here is exactly
# the "reusable API client" row of design.md 12.6's startup table.
app = BedrockAgentCoreApp()
log = app.logger
_agent = build_resolution_agent()


def _packet_from_payload(payload: dict[str, object]) -> CandidatePacket:
    entity_type = payload.get("entity_type")
    raw_identity = payload.get("raw_identity")
    candidates_payload = payload.get("candidates")
    if (
        not isinstance(entity_type, str)
        or not isinstance(raw_identity, dict)
        or not isinstance(candidates_payload, list)
    ):
        raise ValueError(
            "payload must have entity_type: str, raw_identity: dict, candidates: list"
        )
    candidates = [
        Candidate(
            entity_id=str(c["candidate_id"]),
            score=float(c["score"]),
            evidence_codes=tuple(c.get("evidence_codes", [])),
            conflict_codes=tuple(c.get("conflict_codes", [])),
        )
        for c in candidates_payload
    ]
    return CandidatePacket(
        entity_type=entity_type,
        raw_identity={str(k): str(v) for k, v in raw_identity.items()},
        candidates=candidates,
    )


@app.entrypoint
async def invoke(payload: dict[str, object], context: object) -> dict[str, object]:
    """One stateless candidate-resolution proposal. Never merges anything
    itself -- the result is always routed to human review by the caller
    (design.md 10.5's default rollout policy); this entrypoint only adds
    the agent's disposition suggestion and summary to that review case."""
    try:
        packet = _packet_from_payload(payload)
    except (ValueError, KeyError, TypeError) as error:
        return {"error": f"invalid payload: {error}"}

    proposal = propose_resolution(_agent, packet)
    return {
        "disposition": proposal.disposition,
        "candidate_id": proposal.candidate_id,
        "confidence": proposal.confidence,
        "evidence_codes": list(proposal.evidence_codes),
        "conflict_codes": list(proposal.conflict_codes),
        "summary": proposal.summary,
    }


if __name__ == "__main__":
    app.run()
