"""Tests the resolution entrypoint's payload parsing (Task 11.5). Module
import constructs a ``BedrockModel``, which resolves AWS credentials
lazily on an actual API call, not at construction -- so this module is
safely importable in ordinary CI. No real AWS call happens anywhere in
this file.
"""

from __future__ import annotations

import pytest

from xc_platform.agents.agentcore_entrypoints import resolution_entrypoint


def test_module_imports_without_a_real_aws_call() -> None:
    assert resolution_entrypoint.app is not None
    assert resolution_entrypoint._agent is not None


def test_packet_from_payload_parses_a_well_formed_request() -> None:
    packet = resolution_entrypoint._packet_from_payload(
        {
            "entity_type": "athlete",
            "raw_identity": {"first_name": "Liam", "last_name": "Niez"},
            "candidates": [
                {
                    "candidate_id": "a1",
                    "score": 0.6,
                    "evidence_codes": ["SAME_LAST_NAME_ONLY"],
                    "conflict_codes": [],
                }
            ],
        }
    )
    assert packet.entity_type == "athlete"
    assert packet.raw_identity == {"first_name": "Liam", "last_name": "Niez"}
    assert packet.candidates[0].entity_id == "a1"
    assert packet.candidates[0].score == 0.6


def test_packet_from_payload_rejects_a_missing_field() -> None:
    with pytest.raises(ValueError, match="entity_type"):
        resolution_entrypoint._packet_from_payload(
            {"raw_identity": {}, "candidates": []}
        )


def test_packet_from_payload_rejects_the_wrong_shape() -> None:
    with pytest.raises(ValueError, match="candidates"):
        resolution_entrypoint._packet_from_payload(
            {"entity_type": "athlete", "raw_identity": {}, "candidates": "not-a-list"}
        )
