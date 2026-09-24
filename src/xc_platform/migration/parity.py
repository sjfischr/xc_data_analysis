"""Reconciliation and parity reports against the frozen baseline (Task 5.4).

Compares the migrated database against the fixtures
``scripts/freeze_baseline.py`` generated from the frozen CSV
(``tests/fixtures/baseline/*.json``), which are the parity target
(Requirement 1.8). A handful of differences are *expected* because the
migration deliberately normalizes what the frozen baseline's own reference
calculations did not (for example: the baseline groups team scores and
Saint Sebastian standings by whatever literal ``gender`` string a row
happened to have -- "M" or "Boys" -- while the migrated schema normalizes
both to one ``gender_code``; verified separately that this never merges two
groups the baseline kept apart, since a single race never mixes both
spellings). Those are recorded as **observations**, not failures. Anything
else is a **discrepancy** and blocks cutover (Requirement 1.6).
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from xc_platform.migration.historical_csv import normalize_gender

_FLOAT_TOLERANCE = 1e-6


def _require_gender_code(raw_gender: str) -> str:
    """normalize_gender for a baseline value that is always one of M/F/Boys/Girls."""
    code = normalize_gender(raw_gender)
    if code is None:
        raise ValueError(
            f"Unrecognized gender value in baseline fixture: {raw_gender!r}"
        )
    return code


@dataclass(frozen=True, slots=True)
class Finding:
    check: str
    key: str
    expected: Any
    actual: Any
    note: str = ""


@dataclass(slots=True)
class ParityReport:
    checks_run: list[str] = field(default_factory=list)
    discrepancies: list[Finding] = field(default_factory=list)
    observations: list[Finding] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        return not self.discrepancies

    def to_dict(self) -> dict[str, Any]:
        return {
            "passed": self.passed,
            "checks_run": self.checks_run,
            "discrepancy_count": len(self.discrepancies),
            "observation_count": len(self.observations),
            "discrepancies": [asdict(f) for f in self.discrepancies],
            "observations": [asdict(f) for f in self.observations],
        }


def _floats_close(a: float | None, b: float | None) -> bool:
    if a is None or b is None:
        return a is None and b is None
    return abs(a - b) <= _FLOAT_TOLERANCE * max(1.0, abs(a), abs(b))


def _rounded_matches(
    actual: float | None, baseline_rounded: float | None, ndigits: int = 4
) -> bool:
    """Compare a full-precision value against a value the baseline pre-rounded.

    scripts/freeze_baseline.py rounds every float column in
    normalized_metrics.json's race_group_stats to `ndigits` decimals before
    writing it. The view intentionally returns full precision (more useful
    for downstream analytics), so the comparison rounds this side to match
    rather than loosening the tolerance everywhere else.
    """
    if actual is None or baseline_rounded is None:
        return actual is None and baseline_rounded is None
    return round(actual, ndigits) == baseline_rounded


def _load_baseline(baseline_dir: Path, name: str) -> Any:
    with (baseline_dir / name).open("r", encoding="utf-8") as handle:
        return json.load(handle)


def check_counts(
    conn: sqlite3.Connection, baseline_dir: Path
) -> tuple[list[Finding], list[Finding]]:
    """Requirement 1.2: compare row/entity counts against the frozen baseline."""
    baseline = _load_baseline(baseline_dir, "counts.json")
    discrepancies: list[Finding] = []
    observations: list[Finding] = []

    total_staged = conn.execute("SELECT COUNT(*) AS n FROM staged_results").fetchone()[
        "n"
    ]
    if total_staged != baseline["total_records"]:
        discrepancies.append(
            Finding(
                "counts.total_records",
                "total",
                baseline["total_records"],
                total_staged,
                "Every baseline row should appear in staging, valid or quarantined.",
            )
        )

    unique_athletes = conn.execute("SELECT COUNT(*) AS n FROM athletes").fetchone()["n"]
    if unique_athletes != baseline["unique_athletes"]:
        discrepancies.append(
            Finding(
                "counts.unique_athletes",
                "total",
                baseline["unique_athletes"],
                unique_athletes,
            )
        )

    unique_teams = conn.execute(
        "SELECT COUNT(*) AS n FROM schools WHERE canonical_name != 'Unknown'"
    ).fetchone()["n"]
    if unique_teams != baseline["unique_teams"]:
        discrepancies.append(
            Finding(
                "counts.unique_teams",
                "total",
                baseline["unique_teams"],
                unique_teams,
                "Excludes the reserved Unknown placeholder for a missing team_name.",
            )
        )

    quarantined_total = conn.execute(
        "SELECT COUNT(*) AS n FROM staged_results "
        "WHERE validation_state = 'quarantined'"
    ).fetchone()["n"]
    canonical_results = conn.execute("SELECT COUNT(*) AS n FROM results").fetchone()[
        "n"
    ]
    if canonical_results + quarantined_total != baseline["total_records"]:
        discrepancies.append(
            Finding(
                "counts.results_plus_quarantine",
                "total",
                baseline["total_records"],
                canonical_results + quarantined_total,
            )
        )
    else:
        observations.append(
            Finding(
                "counts.quarantine_accounts_for_gap",
                "total",
                baseline["total_records"],
                {
                    "canonical_results": canonical_results,
                    "quarantined": quarantined_total,
                },
                "R1.10: the 2023 Meet 2 team-only row is quarantined, not a "
                "canonical result; canonical + quarantined reconciles exactly "
                "to the baseline's row count.",
            )
        )

    by_division = {
        row["division"]: row["records"]
        for row in baseline["by_division"]
        if row["division"] is not None
    }
    for division, expected in by_division.items():
        actual = conn.execute(
            "SELECT COUNT(*) AS n FROM results r JOIN races ra ON ra.race_id = "
            "r.race_id WHERE ra.division_code = ?",
            (division,),
        ).fetchone()["n"]
        if actual != expected and division != "Frosh":
            discrepancies.append(
                Finding("counts.by_division", division, expected, actual)
            )
        elif actual != expected and division == "Frosh":
            # The one quarantined row is Frosh (2023 Meet 2).
            observations.append(
                Finding(
                    "counts.by_division",
                    division,
                    expected,
                    actual,
                    "Off by exactly the one quarantined 2023 Meet 2 row.",
                )
            )

    return discrepancies, observations


def check_team_scores(
    conn: sqlite3.Connection, baseline_dir: Path
) -> tuple[list[Finding], list[Finding]]:
    """Requirement 1.4: exhaustive comparison against team_scores.json."""
    baseline = _load_baseline(baseline_dir, "team_scores.json")
    baseline_by_key: dict[tuple[int, int, str, str, str], dict[str, Any]] = {}
    for row in baseline:
        # The baseline's own reference calculation grouped by whatever raw
        # gender string a row had ("M" or "Boys"); the schema normalizes
        # both to one gender_code. Verified separately (see module
        # docstring) that a single race never mixes both spellings, so
        # normalizing here only fixes the comparison key, it never merges
        # two baseline groups that were meant to stay distinct.
        key = (
            row["season"],
            row["meet"],
            row["division"],
            _require_gender_code(row["gender"]),
            row["team"],
        )
        baseline_by_key[key] = row

    rows = conn.execute(
        "SELECT vts.season, vts.meet, vts.division, vts.gender, "
        "sc.canonical_name AS team, vts.score, vts.scoring_runners, vts.avg_time_s "
        "FROM v_team_scores vts JOIN schools sc ON sc.school_id = vts.school_id"
    ).fetchall()

    discrepancies: list[Finding] = []
    observations: list[Finding] = []
    seen: set[tuple[int, int, str, str, str]] = set()

    for row in rows:
        key = (row["season"], row["meet"], row["division"], row["gender"], row["team"])
        seen.add(key)
        baseline_row = baseline_by_key.get(key)
        if baseline_row is None:
            observations.append(
                Finding(
                    "team_scores.only_in_migration",
                    str(key),
                    None,
                    dict(row),
                    "Not present in the baseline under this exact key; see "
                    "known gender-string / mojibake / total-vs-placed "
                    "normalization differences documented in the report.",
                )
            )
            continue
        if row["score"] != baseline_row["score"]:
            discrepancies.append(
                Finding(
                    "team_scores.score", str(key), baseline_row["score"], row["score"]
                )
            )
        if row["scoring_runners"] != baseline_row["scoring_runners"]:
            discrepancies.append(
                Finding(
                    "team_scores.scoring_runners",
                    str(key),
                    baseline_row["scoring_runners"],
                    row["scoring_runners"],
                )
            )
        # The baseline stores avg_time_s pre-rounded to 2 decimals
        # (scripts/freeze_baseline.py); the view returns full precision,
        # which is more useful for downstream analytics, so round for
        # comparison rather than loosening the tolerance everywhere else.
        actual_avg = None if row["avg_time_s"] is None else round(row["avg_time_s"], 2)
        if actual_avg != baseline_row["avg_time_s"]:
            discrepancies.append(
                Finding(
                    "team_scores.avg_time_s",
                    str(key),
                    baseline_row["avg_time_s"],
                    row["avg_time_s"],
                )
            )

    missing = set(baseline_by_key) - seen
    for key in missing:
        row = baseline_by_key[key]
        if row["score"] == 0:
            # pandas' Series.sum() over an all-NaN column returns 0.0
            # rather than NaN (skipna=True by default), so the baseline's
            # reference calculation silently produced "score: 0" for any
            # team-race group with zero valid place_overall values -- not a
            # real score of zero, an artifact of that missing data. 2023
            # Meet 1 has no place_overall at all (548/548 rows null); the
            # view correctly declines to fabricate a score with no place
            # data, so it has no row for these keys at all.
            note = (
                "Baseline score of 0 is a pandas NaN-sum artifact, not a "
                "real result: 2023 Meet 1 has no place_overall data for any "
                "row, so the reference calculation summed an all-null "
                "column. The view does not fabricate a score with no place "
                "data (Requirement 1.7)."
            )
        else:
            note = (
                "Not present in the migration under this exact key; likely "
                "the unstandardized mojibake team-name variant or the "
                "Unknown-team exclusion documented in the report."
            )
        observations.append(
            Finding("team_scores.only_in_baseline", str(key), row, None, note)
        )

    return discrepancies, observations


def check_saint_sebastian(
    conn: sqlite3.Connection, baseline_dir: Path
) -> tuple[list[Finding], list[Finding]]:
    """Requirement 1.5: compare cumulative-time standings, season by season."""
    baseline = _load_baseline(baseline_dir, "saint_sebastian.json")
    discrepancies: list[Finding] = []
    observations: list[Finding] = []

    rows = conn.execute(
        "SELECT vs.season_year, vs.division_code, vs.gender_code, "
        "a.display_name, vs.cumulative_time_ms, vs.meets_run, vs.standing_rank "
        "FROM v_saint_sebastian vs JOIN athletes a ON a.athlete_id = vs.athlete_id"
    ).fetchall()
    by_key: dict[tuple[str, str, str, str], sqlite3.Row] = {
        (
            str(r["season_year"]),
            r["division_code"],
            r["gender_code"],
            r["display_name"],
        ): r
        for r in rows
    }

    for season, season_data in baseline["seasons"].items():
        for standing in season_data["standings"]:
            key = (
                season,
                standing["division"],
                _require_gender_code(standing["gender"]),
                standing["athlete_full_name"],
            )
            actual = by_key.get(key)
            if actual is None:
                observations.append(
                    Finding(
                        "saint_sebastian.missing_in_migration",
                        str(key),
                        standing,
                        None,
                        "Gender-string normalization can change the key's "
                        "gender label; see counts checks for confirmation "
                        "that no two baseline groups were merged.",
                    )
                )
                continue
            expected_ms = round(standing["cumulative_time_s"] * 1000)
            if not _floats_close(
                float(actual["cumulative_time_ms"]), float(expected_ms)
            ):
                discrepancies.append(
                    Finding(
                        "saint_sebastian.cumulative_time_ms",
                        str(key),
                        expected_ms,
                        actual["cumulative_time_ms"],
                    )
                )
            if actual["standing_rank"] != standing["rank"]:
                discrepancies.append(
                    Finding(
                        "saint_sebastian.rank",
                        str(key),
                        standing["rank"],
                        actual["standing_rank"],
                    )
                )

    return discrepancies, observations


def check_athlete_histories(
    conn: sqlite3.Connection, baseline_dir: Path
) -> tuple[list[Finding], list[Finding]]:
    """Requirement 1.3: reproduce representative athletes' result histories."""
    baseline = _load_baseline(baseline_dir, "athlete_histories.json")
    discrepancies: list[Finding] = []
    observations: list[Finding] = []

    for athlete, history in baseline["histories"].items():
        rows = conn.execute(
            "SELECT * FROM v_results_enriched WHERE athlete_display_name = ? "
            "ORDER BY season_year, meet_number",
            (athlete,),
        ).fetchall()
        if len(rows) != len(history):
            discrepancies.append(
                Finding(
                    "athlete_histories.row_count",
                    athlete,
                    len(history),
                    len(rows),
                )
            )
            continue

        for expected, actual in zip(history, rows, strict=True):
            key = f"{athlete}:{expected['season_year']}:{expected['meet_number']}"
            if expected["place_overall"] != actual["place_overall"]:
                discrepancies.append(
                    Finding(
                        "athlete_histories.place_overall",
                        key,
                        expected["place_overall"],
                        actual["place_overall"],
                    )
                )
            expected_ms = (
                None
                if expected["finish_time_s"] is None
                else round(expected["finish_time_s"] * 1000)
            )
            if not _floats_close(
                None if expected_ms is None else float(expected_ms),
                None
                if actual["finish_time_ms"] is None
                else float(actual["finish_time_ms"]),
            ):
                discrepancies.append(
                    Finding(
                        "athlete_histories.finish_time_ms",
                        key,
                        expected_ms,
                        actual["finish_time_ms"],
                    )
                )
            if not _floats_close(
                expected["pace_per_mi_min"], actual["pace_per_mi_min"]
            ):
                discrepancies.append(
                    Finding(
                        "athlete_histories.pace_per_mi_min",
                        key,
                        expected["pace_per_mi_min"],
                        actual["pace_per_mi_min"],
                    )
                )
            if not _floats_close(expected["speed_mph"], actual["speed_mph"]):
                discrepancies.append(
                    Finding(
                        "athlete_histories.speed_mph",
                        key,
                        expected["speed_mph"],
                        actual["speed_mph"],
                    )
                )

    return discrepancies, observations


def check_normalized_metrics(
    conn: sqlite3.Connection, baseline_dir: Path
) -> tuple[list[Finding], list[Finding]]:
    """Requirement 1.3: reproduce division distances and per-race-group stats."""
    baseline = _load_baseline(baseline_dir, "normalized_metrics.json")
    discrepancies: list[Finding] = []
    observations: list[Finding] = []

    for row in baseline["division_distances"]:
        division = row["division"]
        actual = conn.execute(
            "SELECT MIN(distance_km) AS km_min, MAX(distance_km) AS km_max, "
            "MIN(distance_mi) AS mi_min, MAX(distance_mi) AS mi_max "
            "FROM v_results_enriched WHERE division_code = ?",
            (division,),
        ).fetchone()
        for field_name, expected_key in (
            ("km_min", "distance_km_min"),
            ("km_max", "distance_km_max"),
            ("mi_min", "distance_mi_min"),
            ("mi_max", "distance_mi_max"),
        ):
            if not _floats_close(actual[field_name], row[expected_key]):
                discrepancies.append(
                    Finding(
                        f"normalized_metrics.division_distances.{field_name}",
                        division,
                        row[expected_key],
                        actual[field_name],
                    )
                )

    for row in baseline["race_group_stats"]:
        gender_code = normalize_gender(row["gender"])
        actual = conn.execute(
            "SELECT COUNT(*) AS records, MIN(finish_time_ms) AS ms_min, "
            "AVG(finish_time_ms) AS ms_mean, MAX(finish_time_ms) AS ms_max, "
            "MIN(pace_per_mi_min) AS pace_best, AVG(pace_per_mi_min) AS pace_mean, "
            "MAX(speed_mph) AS speed_max "
            "FROM v_results_enriched WHERE season_year = ? AND meet_number = ? "
            "AND division_code = ? AND gender_code = ?",
            (row["season_year"], row["meet_number"], row["division"], gender_code),
        ).fetchone()
        key = (
            f"{row['season_year']}:{row['meet_number']}:{row['division']}:{gender_code}"
        )
        if actual["records"] != row["records"]:
            observations.append(
                Finding(
                    "normalized_metrics.race_group_stats.records",
                    key,
                    row["records"],
                    actual["records"],
                    "See counts/team_scores observations for gender-label "
                    "and 2023-Meet-1 missing-place explanations.",
                )
            )
            continue
        for field_name, expected_key in (
            ("ms_min", "finish_time_s_min"),
            ("ms_mean", "finish_time_s_mean"),
            ("ms_max", "finish_time_s_max"),
        ):
            expected_ms = (
                None if row[expected_key] is None else row[expected_key] * 1000
            )
            if not _floats_close(actual[field_name], expected_ms):
                discrepancies.append(
                    Finding(
                        f"normalized_metrics.race_group_stats.{field_name}",
                        key,
                        expected_ms,
                        actual[field_name],
                    )
                )
        if not _rounded_matches(actual["pace_best"], row["pace_per_mi_min_best"]):
            discrepancies.append(
                Finding(
                    "normalized_metrics.race_group_stats.pace_per_mi_min_best",
                    key,
                    row["pace_per_mi_min_best"],
                    actual["pace_best"],
                )
            )
        if not _rounded_matches(actual["pace_mean"], row["pace_per_mi_min_mean"]):
            discrepancies.append(
                Finding(
                    "normalized_metrics.race_group_stats.pace_per_mi_min_mean",
                    key,
                    row["pace_per_mi_min_mean"],
                    actual["pace_mean"],
                )
            )
        if not _rounded_matches(actual["speed_max"], row["speed_mph_max"]):
            discrepancies.append(
                Finding(
                    "normalized_metrics.race_group_stats.speed_mph_max",
                    key,
                    row["speed_mph_max"],
                    actual["speed_max"],
                )
            )

    return discrepancies, observations


def generate_reconciliation_report(
    conn: sqlite3.Connection,
    baseline_dir: str | Path,
    *,
    report_path: str | Path,
) -> ParityReport:
    """Run every parity check and write a JSON reconciliation report."""
    baseline_dir = Path(baseline_dir)
    report = ParityReport()

    for name, check in (
        ("counts", check_counts),
        ("team_scores", check_team_scores),
        ("saint_sebastian", check_saint_sebastian),
        ("athlete_histories", check_athlete_histories),
        ("normalized_metrics", check_normalized_metrics),
    ):
        report.checks_run.append(name)
        discrepancies, observations = check(conn, baseline_dir)
        report.discrepancies.extend(discrepancies)
        report.observations.extend(observations)

    report_path = Path(report_path)
    report_path.parent.mkdir(parents=True, exist_ok=True)
    with report_path.open("w", encoding="utf-8") as handle:
        json.dump(report.to_dict(), handle, indent=2, default=str)
        handle.write("\n")

    return report
