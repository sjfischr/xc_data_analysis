from __future__ import annotations

import json
from typing import Any

import pytest

from xc_platform.agents.resolution_contract import (
    CandidatePacket,
    ResolutionAgentRefusedError,
    parse_resolution_response,
    validate_resolution_response,
)
from xc_platform.resolution.evidence import Candidate


def _packet(candidates: list[Candidate]) -> CandidatePacket:
    return CandidatePacket(
        entity_type="athlete",
        raw_identity={"first_name": "Liam", "last_name": "Niez"},
        candidates=candidates,
    )


def test_valid_match_response_has_no_violations() -> None:
    packet = _packet(
        [Candidate(entity_id="a1", score=0.8, evidence_codes=("X",), conflict_codes=())]
    )
    response = {
        "disposition": "MATCH",
        "candidate_id": "a1",
        "confidence": 0.8,
        "evidence_codes": ["X"],
        "conflict_codes": [],
        "summary": "Exact first/last name and same school-season.",
    }
    assert validate_resolution_response(response, packet=packet) == []


def test_unknown_candidate_id_is_a_violation() -> None:
    packet = _packet([])
    response = {
        "disposition": "MATCH",
        "candidate_id": "ghost-id",
        "confidence": 0.8,
        "evidence_codes": ["X"],
        "conflict_codes": [],
        "summary": "s",
    }
    violations = validate_resolution_response(response, packet=packet)
    assert any("was not in the packet" in v for v in violations)


def test_match_on_a_hard_conflict_candidate_is_a_violation() -> None:
    packet = _packet(
        [
            Candidate(
                entity_id="a1",
                score=0.9,
                evidence_codes=("X",),
                conflict_codes=("CONFIRMED_DISTINCT",),
            )
        ]
    )
    response = {
        "disposition": "MATCH",
        "candidate_id": "a1",
        "confidence": 0.9,
        "evidence_codes": ["X"],
        "conflict_codes": [],
        "summary": "s",
    }
    violations = validate_resolution_response(response, packet=packet)
    assert any("hard conflict" in v for v in violations)


def test_match_with_no_evidence_codes_is_a_violation() -> None:
    packet = _packet(
        [Candidate(entity_id="a1", score=0.8, evidence_codes=(), conflict_codes=())]
    )
    response = {
        "disposition": "MATCH",
        "candidate_id": "a1",
        "confidence": 0.8,
        "evidence_codes": [],
        "conflict_codes": [],
        "summary": "s",
    }
    violations = validate_resolution_response(response, packet=packet)
    assert any("at least one evidence_code" in v for v in violations)


def test_an_unknown_disposition_is_a_violation() -> None:
    packet = _packet([])
    response: dict[str, Any] = {
        "disposition": "MERGE",
        "candidate_id": None,
        "confidence": 0.5,
        "evidence_codes": [],
        "conflict_codes": [],
        "summary": "s",
    }
    violations = validate_resolution_response(response, packet=packet)
    assert any("is not one of" in v for v in violations)


def test_an_oversized_summary_is_a_violation() -> None:
    packet = _packet([])
    response: dict[str, Any] = {
        "disposition": "REVIEW",
        "candidate_id": None,
        "confidence": 0.3,
        "evidence_codes": [],
        "conflict_codes": [],
        "summary": "x" * 501,
    }
    violations = validate_resolution_response(response, packet=packet)
    assert any("exceeds" in v for v in violations)


def test_confidence_out_of_range_is_a_violation() -> None:
    packet = _packet([])
    response: dict[str, Any] = {
        "disposition": "REVIEW",
        "candidate_id": None,
        "confidence": 1.5,
        "evidence_codes": [],
        "conflict_codes": [],
        "summary": "s",
    }
    violations = validate_resolution_response(response, packet=packet)
    assert any("confidence" in v for v in violations)


def test_parse_resolution_response_extracts_json_from_surrounding_text() -> None:
    packet = _packet(
        [Candidate(entity_id="a1", score=0.8, evidence_codes=("X",), conflict_codes=())]
    )
    raw = "Sure, here is my answer:\n" + json.dumps(
        {
            "disposition": "MATCH",
            "candidate_id": "a1",
            "confidence": 0.8,
            "evidence_codes": ["X"],
            "conflict_codes": [],
            "summary": "s",
        }
    )
    proposal = parse_resolution_response(raw, packet=packet)
    assert proposal.disposition == "MATCH"
    assert proposal.candidate_id == "a1"


def test_parse_resolution_response_raises_on_invalid_json() -> None:
    packet = _packet([])
    with pytest.raises(ResolutionAgentRefusedError):
        parse_resolution_response("not json at all", packet=packet)


def test_candidate_packet_to_json_round_trips_candidate_fields() -> None:
    packet = _packet(
        [
            Candidate(
                entity_id="a1",
                score=0.42,
                evidence_codes=("X", "Y"),
                conflict_codes=("Z",),
            )
        ]
    )
    parsed = json.loads(packet.to_json())
    assert parsed["candidates"] == [
        {
            "candidate_id": "a1",
            "score": 0.42,
            "evidence_codes": ["X", "Y"],
            "conflict_codes": ["Z"],
        }
    ]
