"""Task 10.4: pass the curated deterministic regression suite.

This is not a synthetic fixture -- it drives the real deterministic
resolution workflow (:mod:`xc_platform.resolution.workflow`) against the
same curated evidence Task 5.2 already seeded into SQLite from
``name_corrections.csv`` (the repo-root file DUPLICATE_NAMES_REPORT.md
documents), via
:func:`xc_platform.migration.aliases.seed_name_corrections`. Four
requirements from design.md section 10.5 / Requirement 8.9-8.10, each as
its own test:

* honor every curated ``apply`` correction (an alias now exists; a raw
  ``apply`` row's original spelling auto-matches to the corrected
  athlete);
* keep every confirmed-distinct pair separate (the four ``keep`` pairs
  DUPLICATE_NAMES_REPORT.md calls out as "Correctly Identified as
  Different People": Gianna/Giovanni Smolinski, Luke/Blake Pleva,
  Anna/Adam Shewangzaw, Carly/Charlie Buechel);
* route the still-open ``review`` rows to the review queue, not an
  automatic disposition, unless new evidence was added;
* prove a merge reversal reconstructs source identities and results
  without loss (exercised directly against
  :mod:`xc_platform.resolution.decisions`, since ``name_corrections.csv``
  itself has no merge to reverse -- the migration only ever seeds aliases
  and reject decisions, never a merge).
"""

from __future__ import annotations

import csv
import sqlite3
from pathlib import Path

import pytest

from xc_platform.db.repositories.canonical import CanonicalReadRepository
from xc_platform.db.repositories.canonical_write import CanonicalWriteRepository
from xc_platform.db.repositories.resolution import ResolutionRepository
from xc_platform.db.repositories.staging import StagingRepository
from xc_platform.migration.aliases import seed_name_corrections
from xc_platform.migration.canonical_writer import HistoricalCanonicalWriter
from xc_platform.resolution.decisions import (
    approve_athlete_match,
    reverse_athlete_match,
)
from xc_platform.resolution.evidence import AthleteResolutionInput
from xc_platform.resolution.workflow import (
    DISPOSITION_AUTO_CREATED,
    DISPOSITION_AUTO_MATCHED,
    resolve_athlete,
)

CORRECTIONS_CSV = Path(__file__).resolve().parents[3] / "name_corrections.csv"

# The four confirmed-distinct sibling/namesake pairs DUPLICATE_NAMES_REPORT.md
# documents under "Correctly Identified as Different People" -- each a pair
# of self-referential "keep" rows in name_corrections.csv.
CONFIRMED_DISTINCT_PAIRS = [
    ("Gianna Smolinski", "Giovanni Smolinski", "St John the Evangelist"),
    ("Luke Pleva", "Blake Pleva", "Nativity"),
    ("Anna Shewangzaw", "Adam Shewangzaw", "Q of A"),
    ("Carly Buechel", "Charlie Buechel", "Blessed Sacrament"),
]


@pytest.fixture
def seeded(conn: sqlite3.Connection) -> str:
    """Seed the frozen curation exactly as Task 5.2's historical migration
    does, plus one athlete per name in the CSV so the resolution pool has
    something to score against (the migration itself only creates an
    athlete for each `apply` row's *corrected* name -- distinct-pair
    `keep` rows never get an athlete via the migration, since there's no
    row to apply)."""
    source_id = StagingRepository(conn).get_or_create_source(
        source_namespace="historical_csv",
        adapter_type="historical_csv",
        base_domain="local",
    )
    writer = HistoricalCanonicalWriter(conn)
    seed_name_corrections(writer, CORRECTIONS_CSV, source_id=source_id)

    # Only the *corrected* (target) spelling gets a canonical athlete here --
    # never the original/raw spelling of an as-yet-undecided row. A real
    # database only has canonical entities for identities someone already
    # established; conjuring one under every raw spelling in the CSV would
    # let an unresolved ``review`` row "auto-match to itself" and mask the
    # very ambiguity this suite is checking for.
    write = CanonicalWriteRepository(conn)
    with CORRECTIONS_CSV.open(encoding="utf-8", newline="") as handle:
        for row in csv.DictReader(handle):
            name = row["corrected_name"].strip()
            exists = conn.execute(
                "SELECT 1 FROM athletes WHERE display_name = ?", (name,)
            ).fetchone()
            if exists is None:
                write.create_athlete(display_name=name)
    return source_id


def _resolve(
    conn: sqlite3.Connection,
    source_id: str,
    *,
    raw_first: str,
    raw_last: str,
    context_key: str = "",
) -> tuple[str, str | None]:
    canonical = CanonicalReadRepository(conn)
    write = CanonicalWriteRepository(conn)
    resolver = ResolutionRepository(conn)
    outcome = resolve_athlete(
        canonical,
        write,
        resolver,
        source_id=source_id,
        ingest_run_id=None,
        incoming=AthleteResolutionInput(
            first_name=raw_first,
            last_name=raw_last,
            school_id=None,
            season_year=2026,
            grade=None,
            gender_code=None,
            bib=None,
            race_id="regression-race",
        ),
        raw_first_name=raw_first,
        raw_last_name=raw_last,
        context_key=context_key,
    )
    return outcome.disposition, outcome.entity_id


def _apply_rows() -> list[dict[str, str]]:
    with CORRECTIONS_CSV.open(encoding="utf-8", newline="") as handle:
        return [
            row for row in csv.DictReader(handle) if row["action"].strip() == "apply"
        ]


def _review_rows() -> list[dict[str, str]]:
    with CORRECTIONS_CSV.open(encoding="utf-8", newline="") as handle:
        return [
            row for row in csv.DictReader(handle) if row["action"].strip() == "review"
        ]


@pytest.mark.parametrize("row", _apply_rows(), ids=lambda r: r["original_name"])
def test_every_curated_apply_correction_auto_resolves_to_the_corrected_athlete(
    conn: sqlite3.Connection, seeded: str, row: dict[str, str]
) -> None:
    original_first, _, original_last = row["original_name"].strip().partition(" ")
    canonical = CanonicalReadRepository(conn)

    disposition, entity_id = _resolve(
        conn,
        seeded,
        raw_first=original_first,
        raw_last=original_last or original_first,
        context_key=row["team"].strip(),
    )

    assert disposition == DISPOSITION_AUTO_MATCHED
    resolved = canonical.get_athlete(entity_id)  # type: ignore[arg-type]
    assert resolved is not None
    assert resolved.display_name == row["corrected_name"].strip()


@pytest.mark.parametrize(
    "name_a,name_b,team",
    CONFIRMED_DISTINCT_PAIRS,
    ids=[p[0] for p in CONFIRMED_DISTINCT_PAIRS],
)
def test_confirmed_distinct_pairs_never_resolve_into_each_other(
    conn: sqlite3.Connection, seeded: str, name_a: str, name_b: str, team: str
) -> None:
    canonical = CanonicalReadRepository(conn)
    athlete_b = next(
        a
        for a in canonical.find_athletes_by_normalized_last_name(name_b.split()[-1])
        if a.display_name == name_b
    )

    first_a, _, last_a = name_a.partition(" ")
    disposition, entity_id = _resolve(
        conn, seeded, raw_first=first_a, raw_last=last_a, context_key=team
    )

    # Never lands on B's identity, whatever disposition it gets (it should
    # auto-match to A's own pre-existing record).
    assert entity_id != athlete_b.athlete_id
    assert disposition in (DISPOSITION_AUTO_MATCHED, DISPOSITION_AUTO_CREATED)
    if disposition == DISPOSITION_AUTO_MATCHED:
        resolved = canonical.get_athlete(entity_id)  # type: ignore[arg-type]
        assert resolved is not None
        assert resolved.display_name == name_a


def test_review_rows_are_never_auto_resolved_without_new_evidence(
    conn: sqlite3.Connection, seeded: str
) -> None:
    for row in _review_rows():
        original_first, _, original_last = row["original_name"].strip().partition(" ")
        if not original_last:
            continue
        disposition, _ = _resolve(
            conn,
            seeded,
            raw_first=original_first,
            raw_last=original_last,
            context_key=row["team"].strip(),
        )
        assert disposition != DISPOSITION_AUTO_MATCHED, row["original_name"]


def test_merge_reversal_reconstructs_source_identities_and_results_without_loss(
    conn: sqlite3.Connection, seeded: str
) -> None:
    """The CSV's own curated rows never produce a merge (only aliases and
    reject decisions) -- so this drives an actual merge/reverse cycle
    through :mod:`xc_platform.resolution.decisions`, the same path a human
    reviewer approving a real resolution case would take."""
    write = CanonicalWriteRepository(conn)
    canonical = CanonicalReadRepository(conn)
    resolver = ResolutionRepository(conn)

    winner_id = write.create_athlete(display_name="Charlotte Kennedy")
    loser_id = write.create_athlete(display_name="Charlie Kennedy Duplicate")
    school_id = write.create_school(canonical_name="St Agnes")
    season_id = write.upsert_athlete_season(
        athlete_id=loser_id,
        season_year=2026,
        school_id=school_id,
        grade=6,
        gender_code="F",
    )

    case_id = resolver.open_case(
        entity_type="athlete", evidence_json="{}", candidate_entity_id=winner_id
    )
    decision_id = approve_athlete_match(
        resolver,
        write,
        resolution_case_id=case_id,
        winner_athlete_id=winner_id,
        loser_athlete_id=loser_id,
        actor="regression-suite",
        evidence_summary="test merge",
    )
    affected_json = conn.execute(
        "SELECT affected_records_json FROM resolution_decisions "
        "WHERE resolution_decision_id = ?",
        (decision_id,),
    ).fetchone()["affected_records_json"]

    reverse_athlete_match(
        resolver,
        write,
        resolution_case_id=case_id,
        approve_decision_id=decision_id,
        approve_affected_records_json=affected_json,
        actor="regression-suite",
        evidence_summary="test reversal",
    )

    restored = canonical.get_athlete(loser_id)
    assert restored is not None
    assert restored.status == "active"
    restored_seasons = canonical.list_athlete_seasons(loser_id)
    assert [s.athlete_season_id for s in restored_seasons] == [season_id]
