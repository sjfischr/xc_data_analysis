from __future__ import annotations

import sqlite3

from xc_platform.db.repositories.canonical import CanonicalReadRepository
from xc_platform.db.repositories.canonical_write import CanonicalWriteRepository
from xc_platform.db.repositories.resolution import ResolutionRepository
from xc_platform.resolution.decisions import (
    approve_athlete_match,
    reject_athlete_match,
    reverse_athlete_match,
)


def test_approve_merges_and_reverse_restores_source_identities(
    conn: sqlite3.Connection,
) -> None:
    write = CanonicalWriteRepository(conn)
    read = CanonicalReadRepository(conn)
    resolver = ResolutionRepository(conn)

    winner_id = write.create_athlete(display_name="Gwendolyn Fischer")
    loser_id = write.create_athlete(display_name="Gwen Fischer")
    case_id = resolver.open_case(
        entity_type="athlete",
        evidence_json="{}",
        candidate_entity_id=winner_id,
        confidence=0.65,
    )

    decision_id = approve_athlete_match(
        resolver,
        write,
        resolution_case_id=case_id,
        winner_athlete_id=winner_id,
        loser_athlete_id=loser_id,
        actor="reviewer@example.com",
        evidence_summary="Same person, confirmed by team roster.",
    )

    case = resolver.get_case(case_id)
    assert case is not None
    assert case.status == "approved"
    loser = read.get_athlete(loser_id)
    assert loser is not None
    assert loser.status == "merged"
    assert loser.athlete_id == loser_id

    decision_row = conn.execute(
        "SELECT affected_records_json FROM resolution_decisions "
        "WHERE resolution_decision_id = ?",
        (decision_id,),
    ).fetchone()

    reverse_decision_id = reverse_athlete_match(
        resolver,
        write,
        resolution_case_id=case_id,
        approve_decision_id=decision_id,
        approve_affected_records_json=decision_row["affected_records_json"],
        actor="reviewer@example.com",
        evidence_summary="Mistaken merge, reversing.",
    )

    case_after_reverse = resolver.get_case(case_id)
    assert case_after_reverse is not None
    assert case_after_reverse.status == "reversed"
    loser_restored = read.get_athlete(loser_id)
    assert loser_restored is not None
    assert loser_restored.status == "active"

    reverse_row = conn.execute(
        "SELECT reversal_of_decision_id FROM resolution_decisions "
        "WHERE resolution_decision_id = ?",
        (reverse_decision_id,),
    ).fetchone()
    assert reverse_row["reversal_of_decision_id"] == decision_id


def test_reject_leaves_both_athletes_untouched_and_marks_confirmed_distinct(
    conn: sqlite3.Connection,
) -> None:
    write = CanonicalWriteRepository(conn)
    read = CanonicalReadRepository(conn)
    resolver = ResolutionRepository(conn)

    athlete_a = write.create_athlete(display_name="Giovanni Smolinski")
    write.create_athlete(display_name="Gianna Smolinski")
    case_id = resolver.open_case(
        entity_type="athlete",
        evidence_json="{}",
        candidate_entity_id=athlete_a,
    )

    reject_athlete_match(
        resolver,
        resolution_case_id=case_id,
        actor="reviewer@example.com",
        evidence_summary="Confirmed different people (siblings).",
    )

    case = resolver.get_case(case_id)
    assert case is not None
    assert case.status == "rejected"
    assert read.get_athlete(athlete_a).status == "active"  # type: ignore[union-attr]
    assert read.athlete_confirmed_distinct(
        candidate_athlete_id=athlete_a, raw_full_name="irrelevant"
    )
