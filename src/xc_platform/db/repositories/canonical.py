"""Read-only canonical lookups (Task 4.4; extended for Task 10 resolution
candidate lookups and Task 11.1 analytics tool primitives).

This repository never writes. Canonical entities are created by the
resolution workflow once a case is approved (Requirement 8), via
:class:`~xc_platform.db.repositories.canonical_write.CanonicalWriteRepository`
(Task 10.3), not here.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass

from xc_platform.db.repositories.base import BaseRepository


@dataclass(frozen=True, slots=True)
class MeetRecord:
    meet_id: str
    season_year: int
    meet_number: int | None
    name: str
    series: str | None
    meet_date: str | None
    status: str
    venue: str | None


@dataclass(frozen=True, slots=True)
class SchoolRecord:
    school_id: str
    canonical_name: str
    display_name: str
    status: str


@dataclass(frozen=True, slots=True)
class AthleteRecord:
    athlete_id: str
    canonical_first_name: str
    canonical_last_name: str
    display_name: str
    status: str


@dataclass(frozen=True, slots=True)
class AthleteSeasonRecord:
    athlete_season_id: str
    athlete_id: str
    season_year: int
    school_id: str
    grade: int | None
    gender_code: str


@dataclass(frozen=True, slots=True)
class ResultHistoryRecord:
    """One race result plus enough of its race/meet context for a
    progression or pace calculation (design.md 11.1) without a second
    round-trip per row."""

    result_id: str
    race_id: str
    season_year: int
    meet_date: str | None
    meet_name: str
    division_code: str
    gender_code: str
    distance_meters: int | None
    finish_time_ms: int | None
    place_overall: int | None


@dataclass(frozen=True, slots=True)
class TeamScoreRecord:
    """One school's score in one (season, meet, division, gender) race,
    from the parity-tested ``v_team_scores`` view (migrations/0003).
    ``team_rank`` is computed here, not in the view -- the view itself is
    frozen and parity-tested against the historical baseline
    (migration/parity.py), so ranking is layered on top rather than
    risking that guarantee."""

    season_year: int
    meet_number: int
    division_code: str
    gender_code: str
    school_id: str
    school_display_name: str
    score: int
    scoring_runners: int
    avg_time_s: float | None
    team_rank: int


@dataclass(frozen=True, slots=True)
class SchoolRosterEntry:
    """One athlete's roster entry for one school/season (Task 14.3)."""

    athlete_id: str
    athlete_display_name: str
    season_year: int
    grade: int | None
    gender_code: str


@dataclass(frozen=True, slots=True)
class SaintSebastianStandingRecord:
    """One athlete's cumulative-time standing within one (season, division,
    gender) category, from the parity-tested ``v_saint_sebastian`` view
    (migrations/0003) -- ``standing_rank``/``time_back_ms`` are computed
    there already."""

    season_year: int
    division_code: str
    gender_code: str
    athlete_id: str
    athlete_display_name: str
    school_id: str
    school_display_name: str
    cumulative_time_ms: int
    meets_run: int
    standing_rank: int
    time_back_ms: int


@dataclass(frozen=True, slots=True)
class ResultRowRecord:
    """One result flattened with its athlete, school, race, and meet
    context (Task 19.1) -- the single shape every dashboard aggregate
    (overview metrics, leaderboards, profiles) is computed from."""

    result_id: str
    race_id: str
    meet_id: str
    athlete_id: str
    athlete_display_name: str
    school_id: str
    school_display_name: str
    season_year: int
    meet_number: int | None
    meet_name: str
    meet_date: str | None
    division_code: str
    gender_code: str
    distance_meters: int | None
    finish_time_ms: int | None
    place_overall: int | None
    grade: int | None


def _row_to_meet(row: sqlite3.Row) -> MeetRecord:
    return MeetRecord(
        meet_id=row["meet_id"],
        season_year=row["season_year"],
        meet_number=row["meet_number"],
        name=row["name"],
        series=row["series"],
        meet_date=row["meet_date"],
        status=row["status"],
        venue=row["venue"],
    )


def _row_to_school(row: sqlite3.Row) -> SchoolRecord:
    return SchoolRecord(
        school_id=row["school_id"],
        canonical_name=row["canonical_name"],
        display_name=row["display_name"],
        status=row["status"],
    )


def _row_to_athlete(row: sqlite3.Row) -> AthleteRecord:
    return AthleteRecord(
        athlete_id=row["athlete_id"],
        canonical_first_name=row["canonical_first_name"],
        canonical_last_name=row["canonical_last_name"],
        display_name=row["display_name"],
        status=row["status"],
    )


def _row_to_team_score(row: sqlite3.Row) -> TeamScoreRecord:
    return TeamScoreRecord(
        season_year=row["season_year"],
        meet_number=row["meet_number"],
        division_code=row["division_code"],
        gender_code=row["gender_code"],
        school_id=row["school_id"],
        school_display_name=row["school_display_name"],
        score=row["score"],
        scoring_runners=row["scoring_runners"],
        avg_time_s=row["avg_time_s"],
        team_rank=row["team_rank"],
    )


def _row_to_saint_sebastian_standing(row: sqlite3.Row) -> SaintSebastianStandingRecord:
    return SaintSebastianStandingRecord(
        season_year=row["season_year"],
        division_code=row["division_code"],
        gender_code=row["gender_code"],
        athlete_id=row["athlete_id"],
        athlete_display_name=row["athlete_display_name"],
        school_id=row["school_id"],
        school_display_name=row["school_display_name"],
        cumulative_time_ms=row["cumulative_time_ms"],
        meets_run=row["meets_run"],
        standing_rank=row["standing_rank"],
        time_back_ms=row["time_back_ms"],
    )


def _row_to_athlete_season(row: sqlite3.Row) -> AthleteSeasonRecord:
    return AthleteSeasonRecord(
        athlete_season_id=row["athlete_season_id"],
        athlete_id=row["athlete_id"],
        season_year=row["season_year"],
        school_id=row["school_id"],
        grade=row["grade"],
        gender_code=row["gender_code"],
    )


class CanonicalReadRepository(BaseRepository):
    def get_meet(self, meet_id: str) -> MeetRecord | None:
        row = self._conn.execute(
            "SELECT * FROM meets WHERE meet_id = ?", (meet_id,)
        ).fetchone()
        return None if row is None else _row_to_meet(row)

    def list_meets_for_season(self, season_year: int) -> list[MeetRecord]:
        rows = self._conn.execute(
            "SELECT * FROM meets WHERE season_year = ? ORDER BY meet_date",
            (season_year,),
        ).fetchall()
        return [_row_to_meet(row) for row in rows]

    def get_school(self, school_id: str) -> SchoolRecord | None:
        row = self._conn.execute(
            "SELECT * FROM schools WHERE school_id = ?", (school_id,)
        ).fetchone()
        return None if row is None else _row_to_school(row)

    def get_school_by_canonical_name(self, canonical_name: str) -> SchoolRecord | None:
        row = self._conn.execute(
            "SELECT * FROM schools WHERE canonical_name = ?", (canonical_name,)
        ).fetchone()
        return None if row is None else _row_to_school(row)

    def get_athlete(self, athlete_id: str) -> AthleteRecord | None:
        row = self._conn.execute(
            "SELECT * FROM athletes WHERE athlete_id = ?", (athlete_id,)
        ).fetchone()
        return None if row is None else _row_to_athlete(row)

    def resolve_school_alias(
        self, *, source_id: str, normalized_value: str, context_key: str = ""
    ) -> str | None:
        """Return the ``school_id`` for an approved, unambiguous alias.

        Requirement 8.7: an approved alias is reused without another model
        call. Returns ``None`` if there is no unambiguous match (including
        when the only match is flagged ambiguous), signaling the caller to
        fall through to agent-assisted resolution.
        """
        row = self._conn.execute(
            "SELECT school_id FROM school_aliases WHERE source_id = ? "
            "AND normalized_value = ? AND context_key = ? AND is_ambiguous = 0",
            (source_id, normalized_value, context_key),
        ).fetchone()
        return None if row is None else str(row["school_id"])

    def resolve_athlete_alias(
        self, *, source_id: str, normalized_value: str, context_key: str = ""
    ) -> str | None:
        row = self._conn.execute(
            "SELECT athlete_id FROM athlete_aliases WHERE source_id = ? "
            "AND normalized_value = ? AND context_key = ? AND is_ambiguous = 0",
            (source_id, normalized_value, context_key),
        ).fetchone()
        return None if row is None else str(row["athlete_id"])

    def get_source_entity_link(
        self, *, source_id: str, source_entity_type: str, source_entity_id: str
    ) -> tuple[str | None, str | None] | None:
        """Return ``(meet_id, race_id)`` for a known upstream entity ID."""
        row = self._conn.execute(
            "SELECT meet_id, race_id FROM source_entity_links WHERE source_id = ? "
            "AND source_entity_type = ? AND source_entity_id = ?",
            (source_id, source_entity_type, source_entity_id),
        ).fetchone()
        return None if row is None else (row["meet_id"], row["race_id"])

    def list_schools(self) -> list[SchoolRecord]:
        """All active schools -- small and stable enough (a closed set of
        parishes) to score candidate keys against in Python (Task 10.1/10.2)
        rather than requiring an indexed normalized-key column."""
        rows = self._conn.execute(
            "SELECT * FROM schools WHERE status = 'active'"
        ).fetchall()
        return [_row_to_school(row) for row in rows]

    def find_athletes_by_normalized_last_name(
        self, normalized_last_name: str, *, limit: int = 50
    ) -> list[AthleteRecord]:
        """A bounded candidate pool keyed on last name only (design.md
        10.3's evidence pool). First-name/school/grade evidence is scored
        on top of this pool, not used to seed it -- nicknames must not
        silently exclude a real candidate before scoring gets a chance to
        see it (design.md 10.2: nicknames aren't equivalence by default)."""
        rows = self._conn.execute(
            "SELECT * FROM athletes WHERE LOWER(canonical_last_name) = LOWER(?) "
            "AND status = 'active' ORDER BY canonical_first_name LIMIT ?",
            (normalized_last_name, limit),
        ).fetchall()
        return [_row_to_athlete(row) for row in rows]

    def list_athlete_seasons(self, athlete_id: str) -> list[AthleteSeasonRecord]:
        rows = self._conn.execute(
            "SELECT * FROM athlete_seasons WHERE athlete_id = ? ORDER BY season_year",
            (athlete_id,),
        ).fetchall()
        return [_row_to_athlete_season(row) for row in rows]

    def list_athletes_for_school(
        self, school_id: str, *, season_year: int | None = None
    ) -> list[SchoolRosterEntry]:
        conditions = ["asn.school_id = ?"]
        params: list[object] = [school_id]
        if season_year is not None:
            conditions.append("asn.season_year = ?")
            params.append(season_year)
        # conditions are fixed literals above -- values only ever go
        # through `params`, never interpolated.
        rows = self._conn.execute(
            "SELECT asn.athlete_id, a.display_name AS athlete_display_name, "  # noqa: S608
            "asn.season_year, asn.grade, asn.gender_code "
            "FROM athlete_seasons asn "
            "JOIN athletes a ON a.athlete_id = asn.athlete_id "
            f"WHERE {' AND '.join(conditions)} "
            "ORDER BY asn.season_year DESC, a.display_name",
            params,
        ).fetchall()
        return [
            SchoolRosterEntry(
                athlete_id=row["athlete_id"],
                athlete_display_name=row["athlete_display_name"],
                season_year=row["season_year"],
                grade=row["grade"],
                gender_code=row["gender_code"],
            )
            for row in rows
        ]

    def athlete_has_result_in_race(self, *, athlete_id: str, race_id: str) -> bool:
        """True if merging into ``athlete_id`` would collide with a result
        it already has in this race (design.md 10.4's same-race collision
        hard conflict)."""
        row = self._conn.execute(
            "SELECT 1 FROM results WHERE athlete_id = ? AND race_id = ? LIMIT 1",
            (athlete_id, race_id),
        ).fetchone()
        return row is not None

    def athlete_has_bib_in_season(
        self, *, athlete_id: str, season_year: int, bib: str
    ) -> bool:
        row = self._conn.execute(
            "SELECT 1 FROM results r "
            "JOIN races ra ON r.race_id = ra.race_id "
            "JOIN meets m ON ra.meet_id = m.meet_id "
            "WHERE r.athlete_id = ? AND r.bib = ? AND m.season_year = ? LIMIT 1",
            (athlete_id, bib, season_year),
        ).fetchone()
        return row is not None

    def athlete_ids_bound_to_source_identity(
        self, *, source_id: str, normalized_value: str, context_key: str = ""
    ) -> list[str]:
        """Every athlete this exact (source, normalized identity) has ever
        been aliased to -- used to detect design.md 10.4's "source IDs
        already bound to different canonical entities" conflict, including
        an alias flagged ambiguous (excluded from :meth:`resolve_athlete_alias`
        but still evidence of a conflict here)."""
        rows = self._conn.execute(
            "SELECT DISTINCT athlete_id FROM athlete_aliases WHERE source_id = ? "
            "AND normalized_value = ? AND context_key = ?",
            (source_id, normalized_value, context_key),
        ).fetchall()
        return [str(row["athlete_id"]) for row in rows]

    def list_results_for_athlete(self, athlete_id: str) -> list[ResultHistoryRecord]:
        rows = self._conn.execute(
            "SELECT r.result_id, r.race_id, m.season_year, m.meet_date, m.name AS "
            "meet_name, ra.division_code, ra.gender_code, ra.distance_meters, "
            "r.finish_time_ms, r.place_overall "
            "FROM results r "
            "JOIN races ra ON r.race_id = ra.race_id "
            "JOIN meets m ON ra.meet_id = m.meet_id "
            "WHERE r.athlete_id = ? "
            "ORDER BY m.season_year, m.meet_date",
            (athlete_id,),
        ).fetchall()
        return [
            ResultHistoryRecord(
                result_id=row["result_id"],
                race_id=row["race_id"],
                season_year=row["season_year"],
                meet_date=row["meet_date"],
                meet_name=row["meet_name"],
                division_code=row["division_code"],
                gender_code=row["gender_code"],
                distance_meters=row["distance_meters"],
                finish_time_ms=row["finish_time_ms"],
                place_overall=row["place_overall"],
            )
            for row in rows
        ]

    def list_team_scores(
        self,
        *,
        season_year: int | None = None,
        meet_number: int | None = None,
        division_code: str | None = None,
        gender_code: str | None = None,
        school_id: str | None = None,
    ) -> list[TeamScoreRecord]:
        # season/meet/division/gender exactly match team_rank's PARTITION BY
        # key, so filtering on them in the inner query only ever drops whole
        # partitions -- every row RANK() sees within a surviving partition is
        # still the complete field. school_id does not, so it is applied in
        # the outer query instead, *after* ranking: filtering it in the same
        # WHERE as the window function would leave only one school's row per
        # partition, and every rank would silently compute as 1 -- wrong,
        # not just incomplete (found while adding this school_id filter,
        # Task 14.3, 2026-09-23).
        inner_conditions: list[str] = []
        params: list[object] = []
        if season_year is not None:
            inner_conditions.append("vts.season = ?")
            params.append(season_year)
        if meet_number is not None:
            inner_conditions.append("vts.meet = ?")
            params.append(meet_number)
        if division_code is not None:
            inner_conditions.append("vts.division = ?")
            params.append(division_code)
        if gender_code is not None:
            inner_conditions.append("vts.gender = ?")
            params.append(gender_code)
        # `inner_where`/`outer_where` are built only from the fixed
        # condition literals here -- never from a caller-supplied value,
        # which always goes through a `?` placeholder in `params` instead.
        inner_where = (
            f"WHERE {' AND '.join(inner_conditions)}" if inner_conditions else ""
        )
        outer_where = ""
        if school_id is not None:
            outer_where = "WHERE ranked.school_id = ?"
            params.append(school_id)
        rows = self._conn.execute(
            "SELECT ranked.season_year, ranked.meet_number, "  # noqa: S608
            "ranked.division_code, ranked.gender_code, ranked.school_id, "
            "sc.display_name AS school_display_name, ranked.score, "
            "ranked.scoring_runners, ranked.avg_time_s, ranked.team_rank "
            "FROM ("
            "  SELECT vts.season AS season_year, vts.meet AS meet_number, "
            "    vts.division AS division_code, vts.gender AS gender_code, "
            "    vts.school_id, vts.score, vts.scoring_runners, "
            "    vts.avg_time_s, "
            "    ROW_NUMBER() OVER ("
            "      PARTITION BY vts.season, vts.meet, vts.division, vts.gender "
            "      ORDER BY vts.score ASC"
            "    ) AS team_rank "
            "  FROM v_team_scores vts "
            f"  {inner_where}"
            ") ranked "
            "JOIN schools sc ON sc.school_id = ranked.school_id "
            f"{outer_where} "
            "ORDER BY ranked.season_year, ranked.meet_number, "
            "ranked.division_code, ranked.gender_code, ranked.team_rank",
            params,
        ).fetchall()
        return [_row_to_team_score(row) for row in rows]

    def list_saint_sebastian_standings(
        self,
        *,
        season_year: int | None = None,
        division_code: str | None = None,
        gender_code: str | None = None,
    ) -> list[SaintSebastianStandingRecord]:
        conditions: list[str] = []
        params: list[object] = []
        if season_year is not None:
            conditions.append("vs.season_year = ?")
            params.append(season_year)
        if division_code is not None:
            conditions.append("vs.division_code = ?")
            params.append(division_code)
        if gender_code is not None:
            conditions.append("vs.gender_code = ?")
            params.append(gender_code)
        # `where` is built only from the fixed condition literals above --
        # never from a caller-supplied value, which always goes through a
        # `?` placeholder in `params` instead.
        where = f"WHERE {' AND '.join(conditions)}" if conditions else ""
        rows = self._conn.execute(
            "SELECT vs.season_year, vs.division_code, vs.gender_code, "  # noqa: S608
            "vs.athlete_id, vs.athlete_display_name, vs.school_id, "
            "sc.display_name AS school_display_name, vs.cumulative_time_ms, "
            "vs.meets_run, vs.standing_rank, vs.time_back_ms "
            "FROM v_saint_sebastian vs "
            "JOIN schools sc ON sc.school_id = vs.school_id "
            f"{where} "
            "ORDER BY vs.season_year, vs.division_code, vs.gender_code, "
            "vs.standing_rank",
            params,
        ).fetchall()
        return [_row_to_saint_sebastian_standing(row) for row in rows]

    def list_result_rows(
        self,
        *,
        season_year: int | None = None,
        school_id: str | None = None,
        athlete_id: str | None = None,
        division_code: str | None = None,
        gender_code: str | None = None,
        meet_numbers: tuple[int, ...] = (),
        grades: tuple[int, ...] = (),
        race_id: str | None = None,
    ) -> list[ResultRowRecord]:
        """Every matching result, chronological (season, meet number, meet
        date), then by place. Grade is the result's own recorded grade,
        falling back to the athlete's season roster grade."""
        conditions: list[str] = ["a.status = 'active'"]
        params: list[object] = []
        for column, value in (
            ("m.season_year", season_year),
            ("r.school_id", school_id),
            ("r.athlete_id", athlete_id),
            ("ra.division_code", division_code),
            ("ra.gender_code", gender_code),
            ("r.race_id", race_id),
        ):
            if value is not None:
                conditions.append(f"{column} = ?")
                params.append(value)
        if meet_numbers:
            conditions.append(
                f"m.meet_number IN ({', '.join('?' for _ in meet_numbers)})"
            )
            params.extend(meet_numbers)
        if grades:
            conditions.append(
                f"COALESCE(r.grade, asn.grade) IN ({', '.join('?' for _ in grades)})"
            )
            params.extend(grades)
        # Column names and placeholder counts above are fixed literals; every
        # caller-supplied value goes through `params`.
        rows = self._conn.execute(
            "SELECT r.result_id, r.race_id, m.meet_id, r.athlete_id, "  # noqa: S608
            "a.display_name AS athlete_display_name, r.school_id, "
            "sc.display_name AS school_display_name, m.season_year, "
            "m.meet_number, m.name AS meet_name, m.meet_date, ra.division_code, "
            "ra.gender_code, ra.distance_meters, r.finish_time_ms, "
            "r.place_overall, COALESCE(r.grade, asn.grade) AS grade "
            "FROM results r "
            "JOIN races ra ON ra.race_id = r.race_id "
            "JOIN meets m ON m.meet_id = ra.meet_id "
            "JOIN athletes a ON a.athlete_id = r.athlete_id "
            "JOIN schools sc ON sc.school_id = r.school_id "
            "LEFT JOIN athlete_seasons asn ON asn.athlete_id = r.athlete_id "
            "AND asn.season_year = m.season_year AND asn.school_id = r.school_id "
            f"WHERE {' AND '.join(conditions)} "
            "ORDER BY m.season_year, m.meet_number, m.meet_date, "
            "ra.division_code, ra.gender_code, r.place_overall",
            params,
        ).fetchall()
        return [ResultRowRecord(**{k: row[k] for k in row.keys()}) for row in rows]

    def list_season_years(self) -> list[int]:
        rows = self._conn.execute(
            "SELECT DISTINCT season_year FROM meets ORDER BY season_year"
        ).fetchall()
        return [int(row["season_year"]) for row in rows]

    def list_divisions(self) -> list[str]:
        rows = self._conn.execute(
            "SELECT DISTINCT division_code FROM races ORDER BY division_code"
        ).fetchall()
        return [str(row["division_code"]) for row in rows]

    def list_meet_numbers(self) -> list[int]:
        rows = self._conn.execute(
            "SELECT DISTINCT meet_number FROM meets WHERE meet_number IS NOT NULL "
            "ORDER BY meet_number"
        ).fetchall()
        return [int(row["meet_number"]) for row in rows]

    def list_grades(self) -> list[int]:
        rows = self._conn.execute(
            "SELECT grade FROM results WHERE grade IS NOT NULL "
            "UNION SELECT grade FROM athlete_seasons WHERE grade IS NOT NULL "
            "ORDER BY grade"
        ).fetchall()
        return [int(row["grade"]) for row in rows]

    def count_schools(self) -> int:
        row = self._conn.execute(
            "SELECT COUNT(*) AS n FROM schools WHERE status = 'active'"
        ).fetchone()
        return int(row["n"])

    def count_athletes(self) -> int:
        row = self._conn.execute(
            "SELECT COUNT(*) AS n FROM athletes WHERE status = 'active'"
        ).fetchone()
        return int(row["n"])

    def search_athletes_by_name(
        self, query: str, *, limit: int = 20
    ) -> list[AthleteRecord]:
        """Case-insensitive substring search over ``display_name`` -- the
        entity-lookup tool's find, not resolution's exact/candidate match
        (:mod:`xc_platform.resolution.exact_match`/``evidence``), so it
        deliberately does not use those normalization rules."""
        rows = self._conn.execute(
            "SELECT * FROM athletes WHERE status = 'active' "
            "AND display_name LIKE '%' || ? || '%' COLLATE NOCASE "
            "ORDER BY canonical_last_name, canonical_first_name LIMIT ?",
            (query, limit),
        ).fetchall()
        return [_row_to_athlete(row) for row in rows]

    def search_schools_by_name(
        self, query: str, *, limit: int = 20
    ) -> list[SchoolRecord]:
        rows = self._conn.execute(
            "SELECT * FROM schools WHERE status = 'active' "
            "AND display_name LIKE '%' || ? || '%' COLLATE NOCASE "
            "ORDER BY display_name LIMIT ?",
            (query, limit),
        ).fetchall()
        return [_row_to_school(row) for row in rows]

    def athlete_confirmed_distinct(
        self, *, candidate_athlete_id: str, raw_full_name: str
    ) -> bool:
        """True if a human already rejected merging this raw identity into
        this candidate (design.md 10.4's confirmed-distinct hard conflict).

        Checks two shapes of ``resolution_cases`` evidence: cases this
        module opens itself (``candidate_entity_id`` set directly) and the
        legacy ``name_corrections.csv`` migration's "keep" rows (Task 5.2),
        which recorded only ``evidence_json.original_name`` with no
        ``candidate_entity_id`` -- both are real prior decisions and must
        both block an auto-match.
        """
        row = self._conn.execute(
            "SELECT 1 FROM resolution_cases WHERE entity_type = 'athlete' "
            "AND status = 'rejected' AND candidate_entity_id = ? LIMIT 1",
            (candidate_athlete_id,),
        ).fetchone()
        if row is not None:
            return True
        row = self._conn.execute(
            "SELECT 1 FROM resolution_cases WHERE entity_type = 'athlete' "
            "AND status = 'rejected' AND candidate_entity_id IS NULL "
            "AND json_extract(evidence_json, '$.original_name') = ? LIMIT 1",
            (raw_full_name,),
        ).fetchone()
        return row is not None
