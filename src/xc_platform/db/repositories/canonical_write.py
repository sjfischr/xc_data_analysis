"""Canonical writes for the live resolution workflow (Task 10.3).

Distinct from ``HistoricalCanonicalWriter``
(:mod:`xc_platform.migration.canonical_writer`), which is a one-time,
already-resolved historical backfill tool (Task 5.1) that never merges or
splits anything. This repository is the write side entity resolution
(Task 10) and the intake workflow (Task 12) use once a disposition is
reached: creating new canonical entities, and -- the part the historical
writer never needed -- merging two existing canonical athletes together
and reversing that merge (Requirement 8.10).
"""

from __future__ import annotations

import json
from dataclasses import dataclass

from xc_platform.db.errors import RepositoryError
from xc_platform.db.identifiers import new_id, utc_now_iso
from xc_platform.db.repositories.base import BaseRepository
from xc_platform.migration.canonical_writer import split_display_name


@dataclass(frozen=True, slots=True)
class AthleteMergeRecord:
    """Exactly which rows a merge moved, so a reversal can move back only
    those rows -- never "everything currently on the winner" (Requirement
    8.10: a reversal must not sweep up unrelated records added later)."""

    loser_athlete_id: str
    winner_athlete_id: str
    moved_result_ids: tuple[str, ...]
    moved_athlete_season_ids: tuple[str, ...]
    moved_athlete_alias_ids: tuple[str, ...]

    def to_json(self) -> str:
        return json.dumps(
            {
                "loser_athlete_id": self.loser_athlete_id,
                "winner_athlete_id": self.winner_athlete_id,
                "moved_result_ids": list(self.moved_result_ids),
                "moved_athlete_season_ids": list(self.moved_athlete_season_ids),
                "moved_athlete_alias_ids": list(self.moved_athlete_alias_ids),
            }
        )

    @classmethod
    def from_json(cls, payload: str) -> AthleteMergeRecord:
        data = json.loads(payload)
        return cls(
            loser_athlete_id=data["loser_athlete_id"],
            winner_athlete_id=data["winner_athlete_id"],
            moved_result_ids=tuple(data["moved_result_ids"]),
            moved_athlete_season_ids=tuple(data["moved_athlete_season_ids"]),
            moved_athlete_alias_ids=tuple(data["moved_athlete_alias_ids"]),
        )


class CanonicalWriteRepository(BaseRepository):
    def create_school(
        self, *, canonical_name: str, display_name: str | None = None
    ) -> str:
        school_id = new_id()
        now = utc_now_iso()
        with self.transaction() as conn:
            conn.execute(
                "INSERT INTO schools (school_id, canonical_name, display_name, "
                "status, created_at, updated_at) VALUES (?, ?, ?, 'active', ?, ?)",
                (school_id, canonical_name, display_name or canonical_name, now, now),
            )
        return school_id

    def rename_school(self, *, school_id: str, new_name: str) -> tuple[str, str]:
        """Correct a school's name (Task 19.3 owner correction). Results,
        rosters, and aliases stay attached to the same ``school_id``; only
        the canonical and display names change. Returns the previous
        ``(canonical_name, display_name)`` for the audit record."""
        name = new_name.strip()
        if not name:
            raise RepositoryError("new school name must not be empty")
        with self.transaction() as conn:
            row = conn.execute(
                "SELECT canonical_name, display_name FROM schools WHERE school_id = ?",
                (school_id,),
            ).fetchone()
            if row is None:
                raise RepositoryError(f"No school with id {school_id!r}.")
            clash = conn.execute(
                "SELECT school_id FROM schools WHERE canonical_name = ? "
                "AND school_id != ?",
                (name, school_id),
            ).fetchone()
            if clash is not None:
                raise RepositoryError(
                    f"another school is already named {name!r}; merge instead"
                )
            conn.execute(
                "UPDATE schools SET canonical_name = ?, display_name = ?, "
                "updated_at = ? WHERE school_id = ?",
                (name, name, utc_now_iso(), school_id),
            )
        return str(row["canonical_name"]), str(row["display_name"])

    def merge_schools(
        self, *, winner_school_id: str, loser_school_id: str
    ) -> dict[str, list[str]]:
        """Move every result, roster row, and alias off ``loser_school_id``
        onto ``winner_school_id`` and mark the loser merged (Task 19.3 owner
        correction: one school recorded under two names). Refuses when an
        athlete has a roster row at both schools in the same season, which
        the roster's unique index could not hold. Returns the moved ids by
        table, for the audit record."""
        if winner_school_id == loser_school_id:
            raise RepositoryError("Cannot merge a school into itself.")
        conn = self._conn
        for school_id in (winner_school_id, loser_school_id):
            row = conn.execute(
                "SELECT status FROM schools WHERE school_id = ?", (school_id,)
            ).fetchone()
            if row is None or row["status"] != "active":
                raise RepositoryError(f"No active school with id {school_id!r}.")
        clash = conn.execute(
            "SELECT COUNT(*) AS n FROM athlete_seasons l JOIN athlete_seasons w "
            "ON w.athlete_id = l.athlete_id AND w.season_year = l.season_year "
            "WHERE l.school_id = ? AND w.school_id = ?",
            (loser_school_id, winner_school_id),
        ).fetchone()
        if clash["n"]:
            raise RepositoryError(
                f"{clash['n']} athlete season(s) exist at both schools; "
                "resolve those rosters before merging"
            )

        moved = {
            table: [
                str(r[0])
                for r in conn.execute(
                    f"SELECT {key} FROM {table} WHERE school_id = ?",  # noqa: S608 -- fixed identifiers
                    (loser_school_id,),
                ).fetchall()
            ]
            for table, key in (
                ("results", "result_id"),
                ("athlete_seasons", "athlete_season_id"),
                ("school_aliases", "school_alias_id"),
            )
        }
        now = utc_now_iso()
        with self.transaction() as tx:
            tx.execute(
                "UPDATE results SET school_id = ?, updated_at = ? WHERE school_id = ?",
                (winner_school_id, now, loser_school_id),
            )
            tx.execute(
                "UPDATE athlete_seasons SET school_id = ?, updated_at = ? "
                "WHERE school_id = ?",
                (winner_school_id, now, loser_school_id),
            )
            tx.execute(
                "UPDATE school_aliases SET school_id = ? WHERE school_id = ?",
                (winner_school_id, loser_school_id),
            )
            tx.execute(
                "UPDATE schools SET status = 'merged', merged_into_school_id = ?, "
                "updated_at = ? WHERE school_id = ?",
                (winner_school_id, now, loser_school_id),
            )
        return moved

    def create_athlete(self, *, display_name: str) -> str:
        first_name, last_name = split_display_name(display_name)
        athlete_id = new_id()
        now = utc_now_iso()
        with self.transaction() as conn:
            conn.execute(
                "INSERT INTO athletes (athlete_id, canonical_first_name, "
                "canonical_last_name, display_name, status, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, 'active', ?, ?)",
                (athlete_id, first_name, last_name, display_name, now, now),
            )
        return athlete_id

    def add_school_alias(
        self,
        *,
        school_id: str,
        source_id: str,
        raw_value: str,
        normalized_value: str,
        alias_type: str = "source_name",
        context_key: str = "",
        is_ambiguous: bool = False,
    ) -> str:
        alias_id = new_id()
        now = utc_now_iso()
        with self.transaction() as conn:
            conn.execute(
                "INSERT INTO school_aliases (school_alias_id, school_id, source_id, "
                "alias_type, raw_value, normalized_value, context_key, is_ambiguous, "
                "created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    alias_id,
                    school_id,
                    source_id,
                    alias_type,
                    raw_value,
                    normalized_value,
                    context_key,
                    1 if is_ambiguous else 0,
                    now,
                ),
            )
        return alias_id

    def add_athlete_alias(
        self,
        *,
        athlete_id: str,
        source_id: str,
        raw_value: str,
        normalized_value: str,
        alias_type: str = "source_name",
        context_key: str = "",
        is_ambiguous: bool = False,
    ) -> str:
        alias_id = new_id()
        now = utc_now_iso()
        with self.transaction() as conn:
            conn.execute(
                "INSERT INTO athlete_aliases (athlete_alias_id, athlete_id, source_id, "
                "alias_type, raw_value, normalized_value, context_key, is_ambiguous, "
                "created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    alias_id,
                    athlete_id,
                    source_id,
                    alias_type,
                    raw_value,
                    normalized_value,
                    context_key,
                    1 if is_ambiguous else 0,
                    now,
                ),
            )
        return alias_id

    def upsert_athlete_season(
        self,
        *,
        athlete_id: str,
        season_year: int,
        school_id: str,
        grade: int | None,
        gender_code: str,
    ) -> str:
        """Idempotent per (athlete, season, school), like the historical
        writer's version -- first-seen evidence wins for grade/gender
        within a season rather than being silently overwritten."""
        existing = self._conn.execute(
            "SELECT athlete_season_id FROM athlete_seasons WHERE athlete_id = ? "
            "AND season_year = ? AND school_id = ?",
            (athlete_id, season_year, school_id),
        ).fetchone()
        if existing is not None:
            return str(existing["athlete_season_id"])

        athlete_season_id = new_id()
        now = utc_now_iso()
        with self.transaction() as conn:
            conn.execute(
                "INSERT INTO athlete_seasons (athlete_season_id, athlete_id, "
                "season_year, school_id, grade, gender_code, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    athlete_season_id,
                    athlete_id,
                    season_year,
                    school_id,
                    grade,
                    gender_code,
                    now,
                    now,
                ),
            )
        return athlete_season_id

    def merge_athletes(
        self, *, winner_athlete_id: str, loser_athlete_id: str
    ) -> AthleteMergeRecord:
        """Move every result, season, and alias off ``loser_athlete_id`` onto
        ``winner_athlete_id`` and mark the loser merged.

        Relies on the schema's own same-race-per-athlete unique index to
        refuse a merge that would collide two results into one race --
        callers are expected to have already checked
        :meth:`~xc_platform.db.repositories.canonical.CanonicalReadRepository.athlete_has_result_in_race`
        (design.md 10.4), but this is the actual backstop.
        """
        if winner_athlete_id == loser_athlete_id:
            raise RepositoryError("Cannot merge an athlete into itself.")

        conn = self._conn
        result_ids = [
            str(row["result_id"])
            for row in conn.execute(
                "SELECT result_id FROM results WHERE athlete_id = ?",
                (loser_athlete_id,),
            ).fetchall()
        ]
        season_ids = [
            str(row["athlete_season_id"])
            for row in conn.execute(
                "SELECT athlete_season_id FROM athlete_seasons WHERE athlete_id = ?",
                (loser_athlete_id,),
            ).fetchall()
        ]
        alias_ids = [
            str(row["athlete_alias_id"])
            for row in conn.execute(
                "SELECT athlete_alias_id FROM athlete_aliases WHERE athlete_id = ?",
                (loser_athlete_id,),
            ).fetchall()
        ]

        now = utc_now_iso()
        with self.transaction() as tx:
            for result_id in result_ids:
                tx.execute(
                    "UPDATE results SET athlete_id = ?, updated_at = ? "
                    "WHERE result_id = ?",
                    (winner_athlete_id, now, result_id),
                )
            for season_id in season_ids:
                tx.execute(
                    "UPDATE athlete_seasons SET athlete_id = ?, updated_at = ? "
                    "WHERE athlete_season_id = ?",
                    (winner_athlete_id, now, season_id),
                )
            for alias_id in alias_ids:
                tx.execute(
                    "UPDATE athlete_aliases SET athlete_id = ? "
                    "WHERE athlete_alias_id = ?",
                    (winner_athlete_id, alias_id),
                )
            tx.execute(
                "UPDATE athletes SET status = 'merged', merged_into_athlete_id = ?, "
                "updated_at = ? WHERE athlete_id = ?",
                (winner_athlete_id, now, loser_athlete_id),
            )

        return AthleteMergeRecord(
            loser_athlete_id=loser_athlete_id,
            winner_athlete_id=winner_athlete_id,
            moved_result_ids=tuple(result_ids),
            moved_athlete_season_ids=tuple(season_ids),
            moved_athlete_alias_ids=tuple(alias_ids),
        )

    def reverse_athlete_merge(self, record: AthleteMergeRecord) -> None:
        """Undo exactly one recorded merge (Requirement 8.10), moving back
        only the rows that merge actually moved."""
        now = utc_now_iso()
        with self.transaction() as tx:
            for result_id in record.moved_result_ids:
                tx.execute(
                    "UPDATE results SET athlete_id = ?, updated_at = ? "
                    "WHERE result_id = ?",
                    (record.loser_athlete_id, now, result_id),
                )
            for season_id in record.moved_athlete_season_ids:
                tx.execute(
                    "UPDATE athlete_seasons SET athlete_id = ?, updated_at = ? "
                    "WHERE athlete_season_id = ?",
                    (record.loser_athlete_id, now, season_id),
                )
            for alias_id in record.moved_athlete_alias_ids:
                tx.execute(
                    "UPDATE athlete_aliases SET athlete_id = ? "
                    "WHERE athlete_alias_id = ?",
                    (record.loser_athlete_id, alias_id),
                )
            tx.execute(
                "UPDATE athletes SET status = 'active', merged_into_athlete_id = NULL, "
                "updated_at = ? WHERE athlete_id = ?",
                (now, record.loser_athlete_id),
            )

    def split_athlete(
        self,
        *,
        source_athlete_id: str,
        new_display_name: str,
        result_ids_to_move: tuple[str, ...] = (),
        athlete_season_ids_to_move: tuple[str, ...] = (),
        athlete_alias_ids_to_move: tuple[str, ...] = (),
    ) -> str:
        """Create a new athlete and move only the named records onto it,
        for the case where a canonical athlete turns out to represent two
        different real people (Requirement 8.5's "split" action)."""
        new_athlete_id = self.create_athlete(display_name=new_display_name)
        now = utc_now_iso()
        with self.transaction() as tx:
            for result_id in result_ids_to_move:
                tx.execute(
                    "UPDATE results SET athlete_id = ?, updated_at = ? "
                    "WHERE result_id = ? AND athlete_id = ?",
                    (new_athlete_id, now, result_id, source_athlete_id),
                )
            for season_id in athlete_season_ids_to_move:
                tx.execute(
                    "UPDATE athlete_seasons SET athlete_id = ?, updated_at = ? "
                    "WHERE athlete_season_id = ? AND athlete_id = ?",
                    (new_athlete_id, now, season_id, source_athlete_id),
                )
            for alias_id in athlete_alias_ids_to_move:
                tx.execute(
                    "UPDATE athlete_aliases SET athlete_id = ? "
                    "WHERE athlete_alias_id = ? AND athlete_id = ?",
                    (new_athlete_id, alias_id, source_athlete_id),
                )
        return new_athlete_id

    def get_or_create_meet(
        self,
        *,
        season_year: int,
        meet_number: int,
        name: str | None,
        series: str | None,
    ) -> str:
        """Live-ingestion counterpart of
        ``HistoricalCanonicalWriter.get_or_create_meet`` (Task 5.1's
        one-time migration writer) -- duplicated deliberately rather than
        shared, since that writer is documented as historical-backfill-only
        and this repository is the one the live intake workflow (Task 12)
        writes through."""
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

    def insert_result(
        self,
        *,
        source_id: str,
        source_result_id: str | None,
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
        """Insert one canonical result row for a live (non-historical)
        ingest run. Relies on the schema's own uniqueness constraints
        (one result per athlete per race; one row per (source_id,
        source_result_id)) as the final backstop against a duplicate
        commit, on top of the staging-level idempotency checks Task 12.2's
        workflow already performs."""
        result_id = new_id()
        now = utc_now_iso()
        with self.transaction() as conn:
            conn.execute(
                "INSERT INTO results (result_id, source_id, source_result_id, "
                "race_id, athlete_id, school_id, ingest_run_id, finish_time_ms, "
                "original_time_text, place_overall, bib, grade, scored_flag, "
                "created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    result_id,
                    source_id,
                    source_result_id,
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
