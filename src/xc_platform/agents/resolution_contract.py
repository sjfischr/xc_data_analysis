"""The resolution agent's bounded packet and schema-validated response
(Task 11.4, design.md section 10.5).

Pure Python -- no Strands import -- so the contract itself (what a valid
proposal looks like, what makes one invalid) is testable without an agent
or model in the loop, and reusable by both the real agent
(:mod:`xc_platform.agents.resolution_agent`) and any evaluation harness
(Task 11.7).
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from xc_platform.resolution.evidence import Candidate

VALID_DISPOSITIONS = frozenset({"MATCH", "CREATE", "REVIEW"})

# design.md 10.4's hard-conflict vocabulary (xc_platform.resolution.evidence):
# a proposal that names one of these for its chosen candidate is refused
# outright, never merely down-weighted.
_HARD_CONFLICT_CODES = frozenset(
    {
        "SAME_RACE_COLLISION",
        "CONFIRMED_DISTINCT",
        "SOURCE_ID_CONFLICT",
        "IMPOSSIBLE_GRADE_CHRONOLOGY",
        "LOCKED_DECISION_CONFLICT",
    }
)

MAX_SUMMARY_LENGTH = 500


@dataclass(frozen=True, slots=True)
class CandidatePacket:
    """The bounded JSON packet sent to the resolution agent -- exactly the
    deterministic evidence :mod:`xc_platform.resolution.workflow` already
    computed, never raw source rows or other candidates' unrelated data."""

    entity_type: str
    raw_identity: dict[str, str]
    candidates: list[Candidate]

    def to_json(self) -> str:
        return json.dumps(
            {
                "entity_type": self.entity_type,
                "raw_identity": self.raw_identity,
                "candidates": [
                    {
                        "candidate_id": c.entity_id,
                        "score": c.score,
                        "evidence_codes": list(c.evidence_codes),
                        "conflict_codes": list(c.conflict_codes),
                    }
                    for c in self.candidates
                ],
            }
        )


@dataclass(frozen=True, slots=True)
class ResolutionProposal:
    disposition: str
    candidate_id: str | None
    confidence: float
    evidence_codes: tuple[str, ...]
    conflict_codes: tuple[str, ...]
    summary: str


class ResolutionAgentRefusedError(RuntimeError):
    """The agent's raw response failed validation (design.md 10.5: "The
    response is rejected if it references an unknown candidate, violates a
    hard conflict, omits required evidence, or exceeds configured
    length"). Carries every violation found, not just the first."""

    def __init__(self, violations: list[str], raw_response: str) -> None:
        super().__init__("; ".join(violations))
        self.violations = violations
        self.raw_response = raw_response


def validate_resolution_response(
    response: dict[str, Any], *, packet: CandidatePacket
) -> list[str]:
    """Returns every violation found (empty list means valid)."""
    violations: list[str] = []
    known_candidate_ids = {c.entity_id for c in packet.candidates}
    conflicts_by_candidate = {
        c.entity_id: set(c.conflict_codes) for c in packet.candidates
    }

    disposition = response.get("disposition")
    if disposition not in VALID_DISPOSITIONS:
        violations.append(
            f"disposition {disposition!r} is not one of {sorted(VALID_DISPOSITIONS)}"
        )

    candidate_id = response.get("candidate_id")
    if disposition == "MATCH":
        if candidate_id is None:
            violations.append("MATCH requires a candidate_id")
        elif candidate_id not in known_candidate_ids:
            violations.append(f"candidate_id {candidate_id!r} was not in the packet")
        else:
            hard_hits = (
                conflicts_by_candidate.get(candidate_id, set()) & _HARD_CONFLICT_CODES
            )
            if hard_hits:
                violations.append(
                    f"MATCH on candidate {candidate_id!r} violates hard conflict(s) "
                    f"{sorted(hard_hits)}"
                )
    elif candidate_id is not None and candidate_id not in known_candidate_ids:
        violations.append(f"candidate_id {candidate_id!r} was not in the packet")

    evidence_codes = response.get("evidence_codes")
    if not isinstance(evidence_codes, list):
        violations.append("evidence_codes must be a list")
    elif disposition == "MATCH" and not evidence_codes:
        violations.append("MATCH requires at least one evidence_code")

    confidence = response.get("confidence")
    if not isinstance(confidence, int | float) or not (0.0 <= float(confidence) <= 1.0):
        violations.append("confidence must be a number between 0.0 and 1.0")

    summary = response.get("summary")
    if not isinstance(summary, str) or not summary.strip():
        violations.append("summary must be a non-empty string")
    elif len(summary) > MAX_SUMMARY_LENGTH:
        violations.append(f"summary exceeds {MAX_SUMMARY_LENGTH} characters")

    return violations


def parse_resolution_response(
    raw_text: str, *, packet: CandidatePacket
) -> ResolutionProposal:
    """Extract, parse, and validate the agent's JSON response. Raises
    :class:`ResolutionAgentRefusedError` on any violation -- the caller
    (:mod:`xc_platform.agents.resolution_agent`) always falls back to
    ``REVIEW`` on this error rather than propagating a crash."""
    start = raw_text.find("{")
    end = raw_text.rfind("}")
    if start == -1 or end == -1 or end < start:
        raise ResolutionAgentRefusedError(
            ["response did not contain a JSON object"], raw_text
        )
    try:
        parsed = json.loads(raw_text[start : end + 1])
    except json.JSONDecodeError as error:
        raise ResolutionAgentRefusedError(
            [f"invalid JSON: {error}"], raw_text
        ) from error

    if not isinstance(parsed, dict):
        raise ResolutionAgentRefusedError(["response JSON is not an object"], raw_text)

    violations = validate_resolution_response(parsed, packet=packet)
    if violations:
        raise ResolutionAgentRefusedError(violations, raw_text)

    return ResolutionProposal(
        disposition=parsed["disposition"],
        candidate_id=parsed.get("candidate_id"),
        confidence=float(parsed["confidence"]),
        evidence_codes=tuple(parsed["evidence_codes"]),
        conflict_codes=tuple(parsed.get("conflict_codes", [])),
        summary=parsed["summary"],
    )
