from __future__ import annotations

import sqlite3

from xc_platform.db.repositories.canonical import CanonicalReadRepository
from xc_platform.db.repositories.canonical_write import CanonicalWriteRepository
from xc_platform.db.repositories.resolution import ResolutionRepository
from xc_platform.db.repositories.staging import StagingRepository
from xc_platform.resolution.evidence import AthleteResolutionInput
from xc_platform.resolution.workflow import (
    DISPOSITION_AUTO_CREATED,
    DISPOSITION_AUTO_MATCHED,
    DISPOSITION_REVIEW,
    resolve_athlete,
    resolve_school,
)


def _source(conn: sqlite3.Connection) -> str:
    return StagingRepository(conn).get_or_create_source(
        source_namespace="runsignup",
        adapter_type="runsignup",
        base_domain="runsignup.com",
    )


def _incoming(**overrides: object) -> AthleteResolutionInput:
    defaults: dict[str, object] = dict(
        first_name="Gwendolyn",
        last_name="Fischer",
        school_id=None,
        season_year=2026,
        grade=None,
        gender_code=None,
        bib=None,
        race_id="race-1",
    )
    defaults.update(overrides)
    return AthleteResolutionInput(**defaults)  # type: ignore[arg-type]


def test_resolve_athlete_auto_matches_an_existing_exact_name(
    conn: sqlite3.Connection,
) -> None:
    write = CanonicalWriteRepository(conn)
    canonical = CanonicalReadRepository(conn)
    resolver = ResolutionRepository(conn)
    source_id = _source(conn)
    athlete_id = write.create_athlete(display_name="Gwendolyn Fischer")

    outcome = resolve_athlete(
        canonical,
        write,
        resolver,
        source_id=source_id,
        ingest_run_id=None,
        incoming=_incoming(),
        raw_first_name="Gwendolyn",
        raw_last_name="Fischer",
    )
    assert outcome.disposition == DISPOSITION_AUTO_MATCHED
    assert outcome.entity_id == athlete_id
    assert outcome.resolution_case_id is None


def test_resolve_athlete_auto_creates_when_no_candidate_exists(
    conn: sqlite3.Connection,
) -> None:
    write = CanonicalWriteRepository(conn)
    canonical = CanonicalReadRepository(conn)
    resolver = ResolutionRepository(conn)
    source_id = _source(conn)

    outcome = resolve_athlete(
        canonical,
        write,
        resolver,
        source_id=source_id,
        ingest_run_id=None,
        incoming=_incoming(),
        raw_first_name="Gwendolyn",
        raw_last_name="Fischer",
    )
    assert outcome.disposition == DISPOSITION_AUTO_CREATED
    assert outcome.entity_id is not None
    created = canonical.get_athlete(outcome.entity_id)
    assert created is not None
    assert created.display_name == "Gwendolyn Fischer"


def test_resolve_athlete_routes_ambiguous_candidate_to_review(
    conn: sqlite3.Connection,
) -> None:
    """A different first name sharing a last name is never auto-matched or
    auto-created blindly past it -- it must land in the review queue with
    the candidate and its evidence attached (design.md rollout policy)."""
    write = CanonicalWriteRepository(conn)
    canonical = CanonicalReadRepository(conn)
    resolver = ResolutionRepository(conn)
    source_id = _source(conn)
    school_id = write.create_school(canonical_name="St Agnes")
    existing_id = write.create_athlete(display_name="William Niez")
    write.upsert_athlete_season(
        athlete_id=existing_id,
        season_year=2026,
        school_id=school_id,
        grade=6,
        gender_code="M",
    )

    outcome = resolve_athlete(
        canonical,
        write,
        resolver,
        source_id=source_id,
        ingest_run_id=None,
        incoming=_incoming(
            first_name="Liam",
            last_name="Niez",
            school_id=school_id,
            season_year=2026,
            grade=6,
        ),
        raw_first_name="Liam",
        raw_last_name="Niez",
    )
    assert outcome.disposition == DISPOSITION_REVIEW
    assert outcome.resolution_case_id is not None
    case = resolver.get_case(outcome.resolution_case_id)
    assert case is not None
    assert case.candidate_entity_id == existing_id
    assert case.status == "pending"


def test_resolve_athlete_creates_new_identity_when_only_candidate_is_confirmed_distinct(
    conn: sqlite3.Connection,
) -> None:
    """Gianna/Giovanni Smolinski (DUPLICATE_NAMES_REPORT.md): once a human
    has confirmed two same-last-name athletes are different people, a new
    incoming result for one of them must never be routed toward the other,
    even as a review candidate -- it should resolve to its own identity."""
    write = CanonicalWriteRepository(conn)
    canonical = CanonicalReadRepository(conn)
    resolver = ResolutionRepository(conn)
    source_id = _source(conn)

    giovanni_id = write.create_athlete(display_name="Giovanni Smolinski")
    case_id = resolver.open_case(
        entity_type="athlete",
        evidence_json=(
            '{"original_name": "Gianna Smolinski", "notes": "different person"}'
        ),
        candidate_entity_id=giovanni_id,
    )
    resolver.record_decision(
        resolution_case_id=case_id,
        decision_type="reject",
        actor="test",
        evidence_summary="Confirmed distinct from Giovanni.",
        affected_records_json="[]",
    )

    outcome = resolve_athlete(
        canonical,
        write,
        resolver,
        source_id=source_id,
        ingest_run_id=None,
        incoming=_incoming(first_name="Gianna", last_name="Smolinski"),
        raw_first_name="Gianna",
        raw_last_name="Smolinski",
    )
    assert outcome.disposition == DISPOSITION_AUTO_CREATED
    assert outcome.entity_id != giovanni_id


def test_resolve_school_auto_matches_via_normalized_key(
    conn: sqlite3.Connection,
) -> None:
    write = CanonicalWriteRepository(conn)
    canonical = CanonicalReadRepository(conn)
    resolver = ResolutionRepository(conn)
    source_id = _source(conn)
    school_id = write.create_school(canonical_name="St Agnes")

    outcome = resolve_school(
        canonical,
        write,
        resolver,
        source_id=source_id,
        ingest_run_id=None,
        raw_school_name="St. Agnes Parish",
    )
    assert outcome.disposition == DISPOSITION_AUTO_MATCHED
    assert outcome.entity_id == school_id


def test_resolve_school_sends_an_unmatched_name_to_review(
    conn: sqlite3.Connection,
) -> None:
    """Task 19.3: once any school exists, an unmatched name is a review
    question ("BSM" is an abbreviation, not a new parish), never a silent
    create."""
    write = CanonicalWriteRepository(conn)
    canonical = CanonicalReadRepository(conn)
    resolver = ResolutionRepository(conn)
    source_id = _source(conn)
    write.create_school(canonical_name="St Agnes")

    outcome = resolve_school(
        canonical,
        write,
        resolver,
        source_id=source_id,
        ingest_run_id=None,
        raw_school_name="Holy Family",
    )
    assert outcome.disposition == DISPOSITION_REVIEW
    assert outcome.entity_id is None
    assert [s.canonical_name for s in canonical.list_schools()] == ["St Agnes"]


def test_resolve_school_auto_creates_into_an_empty_database(
    conn: sqlite3.Connection,
) -> None:
    write = CanonicalWriteRepository(conn)
    canonical = CanonicalReadRepository(conn)
    outcome = resolve_school(
        canonical,
        write,
        ResolutionRepository(conn),
        source_id=_source(conn),
        ingest_run_id=None,
        raw_school_name="Holy Family",
    )
    assert outcome.disposition == DISPOSITION_AUTO_CREATED
    assert outcome.entity_id is not None
    created = canonical.get_school(outcome.entity_id)
    assert created is not None
    assert created.canonical_name == "Holy Family"
