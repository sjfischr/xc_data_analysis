"""Tests for the live canonical write path (Task 10.3): create, merge, and
reverse. Merge/reverse fidelity is exercised more deeply -- with real
results and seasons attached -- because Requirement 8.10 requires a
reversal to reconstruct source identities and results without loss.
"""

from __future__ import annotations

import sqlite3

import pytest

from xc_platform.db.errors import RepositoryError
from xc_platform.db.identifiers import new_id, utc_now_iso
from xc_platform.db.repositories.canonical import CanonicalReadRepository
from xc_platform.db.repositories.canonical_write import (
    AthleteMergeRecord,
    CanonicalWriteRepository,
)
from xc_platform.db.repositories.ingest import IngestRunRepository
from xc_platform.db.repositories.staging import StagingRepository
from xc_platform.migration.canonical_writer import HistoricalCanonicalWriter


def _make_source(conn: sqlite3.Connection) -> str:
    return StagingRepository(conn).get_or_create_source(
        source_namespace="runsignup",
        adapter_type="runsignup",
        base_domain="runsignup.com",
    )


def _make_race(conn: sqlite3.Connection, *, season_year: int = 2026) -> str:
    """Minimal meet+race, via the historical writer's plain get-or-create
    helpers on the same connection -- no resolution decision involved."""
    historical = HistoricalCanonicalWriter(conn)
    meet_id = historical.get_or_create_meet(
        season_year=season_year, meet_number=1, name="Meet 1", series="NVJCYO"
    )
    return historical.get_or_create_race(
        meet_id=meet_id, division_code="Varsity", gender_code="F", distance_meters=None
    )


def _make_ingest_run(conn: sqlite3.Connection, source_id: str) -> str:
    return IngestRunRepository(conn).create_run(
        source_id=source_id,
        submitted_url="https://runsignup.com/Race/Results/1",
        adapter_type="runsignup",
        correlation_id="corr-1",
    )


def _insert_result(
    conn: sqlite3.Connection,
    *,
    source_id: str,
    race_id: str,
    athlete_id: str,
    school_id: str,
    ingest_run_id: str,
    place_overall: int,
) -> str:
    result_id = new_id()
    now = utc_now_iso()
    conn.execute(
        "INSERT INTO results (result_id, source_id, race_id, athlete_id, "
        "school_id, ingest_run_id, place_overall, created_at, updated_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            result_id,
            source_id,
            race_id,
            athlete_id,
            school_id,
            ingest_run_id,
            place_overall,
            now,
            now,
        ),
    )
    conn.commit()
    return result_id


def test_create_school_and_athlete(conn: sqlite3.Connection) -> None:
    write = CanonicalWriteRepository(conn)
    read = CanonicalReadRepository(conn)

    school_id = write.create_school(canonical_name="St Agnes")
    athlete_id = write.create_athlete(display_name="Jane Doe")

    school = read.get_school(school_id)
    athlete = read.get_athlete(athlete_id)
    assert school is not None and school.canonical_name == "St Agnes"
    assert athlete is not None
    assert (athlete.canonical_first_name, athlete.canonical_last_name) == (
        "Jane",
        "Doe",
    )


def test_find_athletes_by_normalized_last_name_is_case_insensitive(
    conn: sqlite3.Connection,
) -> None:
    write = CanonicalWriteRepository(conn)
    read = CanonicalReadRepository(conn)
    write.create_athlete(display_name="Lucy DeMarr")

    matches = read.find_athletes_by_normalized_last_name("demarr")
    assert [a.display_name for a in matches] == ["Lucy DeMarr"]


def test_merge_moves_results_seasons_and_aliases_and_reversal_restores_them(
    conn: sqlite3.Connection,
) -> None:
    write = CanonicalWriteRepository(conn)
    read = CanonicalReadRepository(conn)
    source_id = _make_source(conn)
    race_id = _make_race(conn)
    ingest_run_id = _make_ingest_run(conn, source_id)
    school_id = write.create_school(canonical_name="St Agnes")

    winner_id = write.create_athlete(display_name="Gwendolyn Fischer")
    loser_id = write.create_athlete(display_name="Gwen Fischer")

    season_id = write.upsert_athlete_season(
        athlete_id=loser_id,
        season_year=2026,
        school_id=school_id,
        grade=6,
        gender_code="F",
    )
    alias_id = write.add_athlete_alias(
        athlete_id=loser_id,
        source_id=source_id,
        raw_value="Gwen Fischer",
        normalized_value="gwen fischer",
    )
    result_id = _insert_result(
        conn,
        source_id=source_id,
        race_id=race_id,
        athlete_id=loser_id,
        school_id=school_id,
        ingest_run_id=ingest_run_id,
        place_overall=3,
    )

    merge_record = write.merge_athletes(
        winner_athlete_id=winner_id, loser_athlete_id=loser_id
    )
    assert merge_record.moved_result_ids == (result_id,)
    assert merge_record.moved_athlete_season_ids == (season_id,)
    assert merge_record.moved_athlete_alias_ids == (alias_id,)

    loser_after = read.get_athlete(loser_id)
    assert loser_after is not None
    assert loser_after.status == "merged"
    assert (
        conn.execute(
            "SELECT athlete_id FROM results WHERE result_id = ?", (result_id,)
        ).fetchone()["athlete_id"]
        == winner_id
    )
    assert (
        conn.execute(
            "SELECT athlete_id FROM athlete_seasons WHERE athlete_season_id = ?",
            (season_id,),
        ).fetchone()["athlete_id"]
        == winner_id
    )
    assert (
        conn.execute(
            "SELECT athlete_id FROM athlete_aliases WHERE athlete_alias_id = ?",
            (alias_id,),
        ).fetchone()["athlete_id"]
        == winner_id
    )

    # Round-trip through JSON, exactly as a stored resolution_decision would.
    reloaded = AthleteMergeRecord.from_json(merge_record.to_json())
    write.reverse_athlete_merge(reloaded)

    loser_restored = read.get_athlete(loser_id)
    assert loser_restored is not None
    assert loser_restored.status == "active"
    assert (
        conn.execute(
            "SELECT athlete_id FROM results WHERE result_id = ?", (result_id,)
        ).fetchone()["athlete_id"]
        == loser_id
    )
    assert (
        conn.execute(
            "SELECT athlete_id FROM athlete_seasons WHERE athlete_season_id = ?",
            (season_id,),
        ).fetchone()["athlete_id"]
        == loser_id
    )
    assert (
        conn.execute(
            "SELECT athlete_id FROM athlete_aliases WHERE athlete_alias_id = ?",
            (alias_id,),
        ).fetchone()["athlete_id"]
        == loser_id
    )


def test_merge_into_self_is_rejected(conn: sqlite3.Connection) -> None:
    write = CanonicalWriteRepository(conn)
    athlete_id = write.create_athlete(display_name="Solo Athlete")
    with pytest.raises(RepositoryError):
        write.merge_athletes(winner_athlete_id=athlete_id, loser_athlete_id=athlete_id)


def test_merge_refuses_same_race_collision(conn: sqlite3.Connection) -> None:
    """The schema's own unique index is the backstop for design.md 10.4's
    same-race hard conflict: merging two athletes who both scored in the
    same race must fail, not silently drop one result."""
    write = CanonicalWriteRepository(conn)
    source_id = _make_source(conn)
    race_id = _make_race(conn)
    ingest_run_id = _make_ingest_run(conn, source_id)
    school_id = write.create_school(canonical_name="A School")

    winner_id = write.create_athlete(display_name="Athlete Winner")
    loser_id = write.create_athlete(display_name="Athlete Loser")

    for athlete_id in (winner_id, loser_id):
        _insert_result(
            conn,
            source_id=source_id,
            race_id=race_id,
            athlete_id=athlete_id,
            school_id=school_id,
            ingest_run_id=ingest_run_id,
            place_overall=1,
        )

    with pytest.raises(sqlite3.IntegrityError):
        write.merge_athletes(winner_athlete_id=winner_id, loser_athlete_id=loser_id)


def test_merge_schools_moves_results_rosters_and_aliases(
    conn: sqlite3.Connection,
) -> None:
    """One school recorded under two names in different seasons (St Rita,
    2026-09-24): everything moves to the kept school and the other is
    marked merged, so it drops out of the active school list."""
    write = CanonicalWriteRepository(conn)
    read = CanonicalReadRepository(conn)
    source_id = _make_source(conn)
    ingest_run_id = _make_ingest_run(conn, source_id)
    race_2023 = _make_race(conn, season_year=2023)
    winner = write.create_school(canonical_name="St Rita")
    loser = write.create_school(canonical_name="St. Rita Parish Alexandria")
    athlete_id = write.create_athlete(display_name="Pat Runner")
    write.upsert_athlete_season(
        athlete_id=athlete_id,
        season_year=2023,
        school_id=loser,
        grade=5,
        gender_code="F",
    )
    write.upsert_athlete_season(
        athlete_id=athlete_id,
        season_year=2024,
        school_id=winner,
        grade=6,
        gender_code="F",
    )
    result_id = _insert_result(
        conn,
        source_id=source_id,
        race_id=race_2023,
        athlete_id=athlete_id,
        school_id=loser,
        ingest_run_id=ingest_run_id,
        place_overall=3,
    )
    write.add_school_alias(
        school_id=loser,
        source_id=source_id,
        raw_value="St. Rita Parish Alexandria",
        normalized_value="st rita parish alexandria",
    )

    moved = write.merge_schools(winner_school_id=winner, loser_school_id=loser)

    assert moved["results"] == [result_id]
    assert len(moved["athlete_seasons"]) == 1
    assert len(moved["school_aliases"]) == 1
    assert {r["school_id"] for r in conn.execute("SELECT school_id FROM results")} == {
        winner
    }
    assert {
        r["school_id"] for r in conn.execute("SELECT school_id FROM athlete_seasons")
    } == {winner}
    assert [s.school_id for s in read.list_schools()] == [winner]
    row = conn.execute(
        "SELECT status, merged_into_school_id FROM schools WHERE school_id = ?",
        (loser,),
    ).fetchone()
    assert (row["status"], row["merged_into_school_id"]) == ("merged", winner)


def test_merge_schools_refuses_an_athlete_season_at_both(
    conn: sqlite3.Connection,
) -> None:
    write = CanonicalWriteRepository(conn)
    winner = write.create_school(canonical_name="School A")
    loser = write.create_school(canonical_name="School B")
    athlete_id = write.create_athlete(display_name="Pat Runner")
    for school_id in (winner, loser):
        write.upsert_athlete_season(
            athlete_id=athlete_id,
            season_year=2024,
            school_id=school_id,
            grade=6,
            gender_code="F",
        )
    with pytest.raises(RepositoryError, match="both schools"):
        write.merge_schools(winner_school_id=winner, loser_school_id=loser)
    assert len(CanonicalReadRepository(conn).list_schools()) == 2
    with pytest.raises(RepositoryError):
        write.merge_schools(winner_school_id=winner, loser_school_id=winner)


def test_split_athlete_moves_only_named_records(conn: sqlite3.Connection) -> None:
    write = CanonicalWriteRepository(conn)
    read = CanonicalReadRepository(conn)
    school_id = write.create_school(canonical_name="A School")

    combined_id = write.create_athlete(display_name="Combined Person")
    season_to_move = write.upsert_athlete_season(
        athlete_id=combined_id,
        season_year=2025,
        school_id=school_id,
        grade=5,
        gender_code="M",
    )
    season_to_keep = write.upsert_athlete_season(
        athlete_id=combined_id,
        season_year=2026,
        school_id=school_id,
        grade=6,
        gender_code="M",
    )

    new_athlete_id = write.split_athlete(
        source_athlete_id=combined_id,
        new_display_name="Split-Off Person",
        athlete_season_ids_to_move=(season_to_move,),
    )

    moved = read.list_athlete_seasons(new_athlete_id)
    remaining = read.list_athlete_seasons(combined_id)
    assert [s.athlete_season_id for s in moved] == [season_to_move]
    assert [s.athlete_season_id for s in remaining] == [season_to_keep]
