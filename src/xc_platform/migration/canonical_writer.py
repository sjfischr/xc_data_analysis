"""Direct canonical writes for the one-time historical backfill (Task 5.1).

See the package docstring for why this bypasses the staged/resolved write
path live ingestion (Task 6+) must use: the frozen baseline's identity is
already fully resolved, so every method here is an idempotent
get-or-create-by-natural-key operation, not a resolution decision.
"""

from __future__ import annotations

from xc_platform.db.identifiers import new_id, utc_now_iso
from xc_platform.db.repositories.base import BaseRepository
from xc_platform.migration.historical_csv import UNKNOWN_SCHOOL_NAME


def split_display_name(display_name: str) -> tuple[str, str]:
    """Split a full name into (first, last) for the required schema columns.

    Naive last-token split: "Mary Jane Watson" -> ("Mary Jane", "Watson").
    The frozen baseline and every downstream view key identity on the full
    ``display_name`` string, never on this split, so this is cosmetic only
    -- good enough to populate required NOT NULL columns, not an attempt at
    real name parsing.
    """
    parts = display_name.strip().split()
    if len(parts) <= 1:
        return display_name.strip(), ""
    return " ".join(parts[:-1]), parts[-1]


class HistoricalCanonicalWriter(BaseRepository):
    def get_or_create_school(self, canonical_name: str) -> str:
        existing = self._conn.execute(
            "SELECT school_id FROM schools WHERE canonical_name = ?",
            (canonical_name,),
        ).fetchone()
        if existing is not None:
            return str(existing["school_id"])

        school_id = new_id()
        now = utc_now_iso()
        with self.transaction() as conn:
            conn.execute(
                "INSERT INTO schools (school_id, canonical_name, display_name, "
                "created_at, updated_at) VALUES (?, ?, ?, ?, ?)",
                (school_id, canonical_name, canonical_name, now, now),
            )
        return school_id

    def get_unknown_school(self) -> str:
        """Return the reserved placeholder school for a missing team name."""
        return self.get_or_create_school(UNKNOWN_SCHOOL_NAME)

    def get_or_create_athlete(self, display_name: str) -> str:
        existing = self._conn.execute(
            "SELECT athlete_id FROM athletes WHERE display_name = ?",
            (display_name,),
        ).fetchone()
        if existing is not None:
            return str(existing["athlete_id"])

        first_name, last_name = split_display_name(display_name)
        athlete_id = new_id()
        now = utc_now_iso()
        with self.transaction() as conn:
            conn.execute(
                "INSERT INTO athletes (athlete_id, canonical_first_name, "
                "canonical_last_name, display_name, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (athlete_id, first_name, last_name, display_name, now, now),
            )
        return athlete_id

    def get_or_create_meet(
        self,
        *,
        season_year: int,
        meet_number: int,
        name: str | None,
        series: str | None,
    ) -> str:
        existing = self._conn.execute(
            "SELECT meet_id FROM meets WHERE season_year = ? AND meet_number = ?",
            (season_year, meet_number),
        ).fetchone()
        if existing is not None:
            return str(existing["meet_id"])

        meet_id = new_id()
        now = utc_now_iso()
        with self.transaction() as conn:
            conn.execute(
                "INSERT INTO meets (meet_id, season_year, meet_number, name, "
                "series, status, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, 'completed', ?, ?)",
                (
                    meet_id,
                    season_year,
                    meet_number,
                    name or f"Meet {meet_number}",
                    series,
                    now,
                    now,
                ),
            )
        return meet_id

    def get_or_create_race(
        self,
        *,
        meet_id: str,
        division_code: str,
        gender_code: str,
        distance_meters: int | None,
    ) -> str:
        existing = self._conn.execute(
            "SELECT race_id FROM races WHERE meet_id = ? AND division_code = ? "
            "AND gender_code = ?",
            (meet_id, division_code, gender_code),
        ).fetchone()
        if existing is not None:
            return str(existing["race_id"])

        race_id = new_id()
        now = utc_now_iso()
        with self.transaction() as conn:
            conn.execute(
                "INSERT INTO races (race_id, meet_id, division_code, "
                "gender_code, distance_meters, status, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, 'completed', ?, ?)",
                (
                    race_id,
                    meet_id,
                    division_code,
                    gender_code,
                    distance_meters,
                    now,
                    now,
                ),
            )
        return race_id

    def upsert_athlete_season(
        self,
        *,
        athlete_id: str,
        season_year: int,
        school_id: str,
        grade: int | None,
        gender_code: str,
    ) -> None:
        """Record season/school/grade evidence, first-seen wins.

        The frozen baseline is a set of independent result rows, not an
        explicit roster, so the first row seen for
        (athlete, season, school) sets grade/gender; later rows for the
        same triple are not expected to disagree and are not overwritten.
        """
        existing = self._conn.execute(
            "SELECT athlete_season_id FROM athlete_seasons WHERE athlete_id = ? "
            "AND season_year = ? AND school_id = ?",
            (athlete_id, season_year, school_id),
        ).fetchone()
        if existing is not None:
            return

        now = utc_now_iso()
        with self.transaction() as conn:
            conn.execute(
                "INSERT INTO athlete_seasons (athlete_season_id, athlete_id, "
                "season_year, school_id, grade, gender_code, created_at, "
                "updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    new_id(),
                    athlete_id,
                    season_year,
                    school_id,
                    grade,
                    gender_code,
                    now,
                    now,
                ),
            )

    def insert_result(
        self,
        *,
        source_id: str,
        race_id: str,
        athlete_id: str,
        school_id: str,
        ingest_run_id: str,
        finish_time_ms: int | None,
        original_time_text: str | None,
        place_overall: int | None,
        bib: str | None,
        grade: int | None,
        scored_flag: str,
    ) -> str:
        result_id = new_id()
        now = utc_now_iso()
        with self.transaction() as conn:
            conn.execute(
                "INSERT INTO results (result_id, source_id, race_id, "
                "athlete_id, school_id, ingest_run_id, finish_time_ms, "
                "original_time_text, place_overall, bib, grade, scored_flag, "
                "created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    result_id,
                    source_id,
                    race_id,
                    athlete_id,
                    school_id,
                    ingest_run_id,
                    finish_time_ms,
                    original_time_text,
                    place_overall,
                    bib,
                    grade,
                    scored_flag,
                    now,
                    now,
                ),
            )
        return result_id
