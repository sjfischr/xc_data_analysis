from __future__ import annotations

import sqlite3

from xc_platform.db.repositories.canonical import CanonicalReadRepository
from xc_platform.db.repositories.canonical_write import CanonicalWriteRepository
from xc_platform.db.repositories.staging import StagingRepository
from xc_platform.migration.aliases import normalize_alias_value
from xc_platform.resolution.exact_match import (
    resolve_athlete_exact,
    resolve_school_exact,
)


def _source(conn: sqlite3.Connection) -> str:
    return StagingRepository(conn).get_or_create_source(
        source_namespace="runsignup",
        adapter_type="runsignup",
        base_domain="runsignup.com",
    )


def test_resolve_athlete_exact_via_approved_alias(conn: sqlite3.Connection) -> None:
    write = CanonicalWriteRepository(conn)
    read = CanonicalReadRepository(conn)
    source_id = _source(conn)

    athlete_id = write.create_athlete(display_name="Gwendolyn Fischer")
    write.add_athlete_alias(
        athlete_id=athlete_id,
        source_id=source_id,
        raw_value="Gwen Fischer",
        normalized_value=normalize_alias_value("Gwen Fischer"),
    )

    resolved = resolve_athlete_exact(
        read, source_id=source_id, raw_first_name="Gwen", raw_last_name="Fischer"
    )
    assert resolved == athlete_id


def test_resolve_athlete_exact_via_unambiguous_normalized_key(
    conn: sqlite3.Connection,
) -> None:
    write = CanonicalWriteRepository(conn)
    read = CanonicalReadRepository(conn)
    source_id = _source(conn)

    athlete_id = write.create_athlete(display_name="Lucy DeMarr")

    # No alias exists yet, but "lucy demarr" normalizes to exactly one
    # existing canonical athlete -- step 3 should still resolve it.
    resolved = resolve_athlete_exact(
        read, source_id=source_id, raw_first_name="Lucy", raw_last_name="Demarr"
    )
    assert resolved == athlete_id


def test_resolve_athlete_exact_returns_none_when_ambiguous(
    conn: sqlite3.Connection,
) -> None:
    write = CanonicalWriteRepository(conn)
    read = CanonicalReadRepository(conn)
    source_id = _source(conn)

    write.create_athlete(display_name="John Smith")
    write.create_athlete(display_name="John Smith")

    resolved = resolve_athlete_exact(
        read, source_id=source_id, raw_first_name="John", raw_last_name="Smith"
    )
    assert resolved is None


def test_resolve_athlete_exact_does_not_fold_nicknames(
    conn: sqlite3.Connection,
) -> None:
    write = CanonicalWriteRepository(conn)
    read = CanonicalReadRepository(conn)
    source_id = _source(conn)

    write.create_athlete(display_name="Gwendolyn Fischer")

    # "Gwen" has no approved alias yet, and normalizes to a different key
    # than "Gwendolyn" -- so this must NOT auto-resolve.
    resolved = resolve_athlete_exact(
        read, source_id=source_id, raw_first_name="Gwen", raw_last_name="Fischer"
    )
    assert resolved is None


def test_resolve_school_exact_via_normalized_key(conn: sqlite3.Connection) -> None:
    write = CanonicalWriteRepository(conn)
    read = CanonicalReadRepository(conn)
    source_id = _source(conn)

    school_id = write.create_school(canonical_name="St Agnes")

    resolved = resolve_school_exact(
        read, source_id=source_id, raw_school_name="St. Agnes Parish"
    )
    assert resolved == school_id


def test_resolve_school_exact_returns_none_for_unknown_school(
    conn: sqlite3.Connection,
) -> None:
    read = CanonicalReadRepository(conn)
    source_id = _source(conn)

    resolved = resolve_school_exact(
        read, source_id=source_id, raw_school_name="New Parish"
    )
    assert resolved is None
