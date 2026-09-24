from __future__ import annotations

import json

from xc_platform.agents._fake_model import FakeModel, TextTurn
from xc_platform.agents.resolution_agent import (
    build_resolution_agent,
    propose_resolution,
)
from xc_platform.agents.resolution_contract import CandidatePacket
from xc_platform.resolution.evidence import Candidate


def _packet() -> CandidatePacket:
    return CandidatePacket(
        entity_type="athlete",
        raw_identity={"first_name": "Liam", "last_name": "Niez"},
        candidates=[
            Candidate(
                entity_id="a1",
                score=0.75,
                evidence_codes=("SAME_LAST_NAME_ONLY",),
                conflict_codes=(),
            )
        ],
    )


def test_propose_resolution_returns_a_valid_review_proposal() -> None:
    fake = FakeModel(
        script=[
            TextTurn(
                text=json.dumps(
                    {
                        "disposition": "REVIEW",
                        "candidate_id": "a1",
                        "confidence": 0.4,
                        "evidence_codes": ["SAME_LAST_NAME_ONLY"],
                        "conflict_codes": [],
                        "summary": "Only the last name matches; first name differs.",
                    }
                )
            )
        ]
    )
    agent = build_resolution_agent(model=fake)
    proposal = propose_resolution(agent, _packet())

    assert proposal.disposition == "REVIEW"
    assert proposal.candidate_id == "a1"
    assert "last name" in proposal.summary.lower()


def test_propose_resolution_degrades_to_review_on_an_invalid_model_response() -> None:
    fake = FakeModel(script=[TextTurn(text="I think it's probably a match!")])
    agent = build_resolution_agent(model=fake)
    proposal = propose_resolution(agent, _packet())

    assert proposal.disposition == "REVIEW"
    assert "refused" in proposal.summary.lower()


def test_propose_resolution_degrades_to_review_on_a_hard_conflict_match() -> None:
    packet = CandidatePacket(
        entity_type="athlete",
        raw_identity={"first_name": "Gianna", "last_name": "Smolinski"},
        candidates=[
            Candidate(
                entity_id="a1",
                score=0.9,
                evidence_codes=("SAME_LAST_NAME_ONLY",),
                conflict_codes=("CONFIRMED_DISTINCT",),
            )
        ],
    )
    fake = FakeModel(
        script=[
            TextTurn(
                text=json.dumps(
                    {
                        "disposition": "MATCH",
                        "candidate_id": "a1",
                        "confidence": 0.9,
                        "evidence_codes": ["SAME_LAST_NAME_ONLY"],
                        "conflict_codes": [],
                        "summary": "Looks like the same person.",
                    }
                )
            )
        ]
    )
    agent = build_resolution_agent(model=fake)
    proposal = propose_resolution(agent, packet)

    # The model tried to force a match past a hard conflict -- the contract
    # refuses it and the caller sees REVIEW, never the model's MATCH.
    assert proposal.disposition == "REVIEW"
    assert "hard conflict" in proposal.summary.lower()
