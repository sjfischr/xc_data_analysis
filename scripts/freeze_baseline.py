"""Freeze and fingerprint the historical baseline (spec Task 1.4).

Reads the canonical ``data/merged/season_results.csv`` plus its supporting
artifacts (name corrections, team-name mappings, saved HTML pages, raw CSVs,
and data-quality reports) and writes a machine-readable baseline under
``tests/fixtures/baseline/``. Later parity tasks (Task 5.x) compare the
migrated database against these frozen numbers.

Known data gaps are captured as-is (null counts, missing meets); nothing is
normalized away. Usage:

    python scripts/freeze_baseline.py
"""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pandas as pd

REPO_ROOT = Path(__file__).resolve().parent.parent
OUTPUT_DIR = REPO_ROOT / "tests" / "fixtures" / "baseline"

CANONICAL_CSV = REPO_ROOT / "data" / "merged" / "season_results.csv"
CANONICAL_RATIONALE = (
    "data/merged/season_results.csv is the file the production Streamlit "
    "dashboard loads (dashboard.py line 60). Selected on 2026-09-20 and "
    "signed off by the owner on 2026-09-20. "
    "season_results_corrected.csv is byte-identical in size and mtime; all "
    "other variants are intermediate artifacts of the name-correction passes."
)

SUPPORTING_FILES = [
    "name_corrections.csv",
    "standardize_team_names.py",  # contains the team-name mapping table
    "data/merged/name_mapping.csv",
    "data/merged/mojibake_names.csv",
]

QUALITY_REPORTS = [
    "2023_DATA_FIX_REPORT.md",
    "DUPLICATE_NAMES_REPORT.md",
    "MEET2_DATA_GAP_ANALYSIS.md",
    "MHTML_PROCESSING_REPORT.md",
    "DATASET_COLUMNS.md",
]

# Directories whose every file is fingerprinted (historical source pages).
SOURCE_DIRS = ["data/pages", "data/raw"]

SAINT_SEBASTIAN_REQUIRED_MEETS = 3  # mirrors dashboard.py


def sha256_of(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def fingerprint(path: Path) -> dict[str, Any]:
    stat = path.stat()
    return {
        "path": path.relative_to(REPO_ROOT).as_posix(),
        "sha256": sha256_of(path),
        "bytes": stat.st_size,
        "modified_utc": datetime.fromtimestamp(stat.st_mtime, tz=UTC).isoformat(
            timespec="seconds"
        ),
    }


def jsonable(value: Any) -> Any:
    """Convert numpy/pandas scalars to plain JSON types."""
    if pd.isna(value):
        return None
    if hasattr(value, "item"):
        return value.item()
    return value


def frame_records(frame: pd.DataFrame) -> list[dict[str, Any]]:
    return [
        {key: jsonable(val) for key, val in record.items()}
        for record in frame.to_dict(orient="records")
    ]


def build_manifest(df: pd.DataFrame) -> dict[str, Any]:
    canonical = fingerprint(CANONICAL_CSV)
    canonical["rows"] = len(df)
    canonical["columns"] = list(df.columns)
    canonical["dtypes"] = {col: str(dtype) for col, dtype in df.dtypes.items()}

    supporting = [fingerprint(REPO_ROOT / rel) for rel in SUPPORTING_FILES]
    reports = [fingerprint(REPO_ROOT / rel) for rel in QUALITY_REPORTS]

    sources = []
    for rel in SOURCE_DIRS:
        directory = REPO_ROOT / rel
        for path in sorted(directory.iterdir()):
            if path.is_file():
                sources.append(fingerprint(path))

    return {
        "task": "1.4 Freeze and fingerprint the historical baseline",
        "generated_utc": datetime.now(tz=UTC).isoformat(timespec="seconds"),
        "canonical_dataset": canonical,
        "canonical_selection_rationale": CANONICAL_RATIONALE,
        "supporting_files": supporting,
        "data_quality_reports": reports,
        "historical_sources": sources,
    }


def build_counts(df: pd.DataFrame) -> dict[str, Any]:
    def group_sizes(cols: list[str]) -> list[dict[str, Any]]:
        grouped = df.groupby(cols, dropna=False).size().reset_index(name="records")
        grouped = grouped.sort_values(cols, na_position="last")
        return frame_records(grouped)

    return {
        "total_records": len(df),
        "unique_athletes": int(df["athlete_full_name"].nunique()),
        "unique_teams": int(df["team_name"].nunique()),
        "by_season": group_sizes(["season_year"]),
        "by_season_meet": group_sizes(["season_year", "meet_number"]),
        "by_season_meet_division_gender": group_sizes(
            ["season_year", "meet_number", "division", "gender"]
        ),
        "by_division": group_sizes(["division"]),
        "by_gender": group_sizes(["gender"]),
        "by_school": group_sizes(["team_name"]),
        "athletes_per_season": frame_records(
            df.groupby("season_year")["athlete_full_name"]
            .nunique()
            .reset_index(name="unique_athletes")
        ),
        # Known data gaps, preserved as found (R1.6, R19.2).
        "null_counts_by_column": {col: int(df[col].isna().sum()) for col in df.columns},
    }


def build_team_scores(df: pd.DataFrame) -> list[dict[str, Any]]:
    """Replicate dashboard.py cross-country scoring exactly.

    Per (season, meet, division, gender): a team scores only when it has at
    least five finishers; the score is the sum of its top five overall places.
    """
    scores: list[dict[str, Any]] = []
    for season in sorted(df["season_year"].dropna().unique()):
        season_df = df[df["season_year"] == season]
        for meet in sorted(season_df["meet_number"].dropna().unique()):
            for division in sorted(season_df["division"].dropna().unique()):
                for gender in sorted(season_df["gender"].dropna().unique()):
                    race_df = season_df[
                        (season_df["meet_number"] == meet)
                        & (season_df["division"] == division)
                        & (season_df["gender"] == gender)
                    ].sort_values("place_overall")
                    for team in sorted(race_df["team_name"].dropna().unique()):
                        runners = race_df[race_df["team_name"] == team].head(5)
                        if len(runners) >= 5:
                            avg_time = runners["finish_time_s"].mean()
                            scores.append(
                                {
                                    "season": int(season),
                                    "meet": int(meet),
                                    "division": division,
                                    "gender": gender,
                                    "team": team,
                                    "score": int(runners["place_overall"].sum()),
                                    "scoring_runners": len(runners),
                                    "avg_time_s": jsonable(round(avg_time, 2))
                                    if pd.notna(avg_time)
                                    else None,
                                }
                            )
    return scores


def build_saint_sebastian(df: pd.DataFrame) -> dict[str, Any]:
    """Replicate the dashboard's Saint Sebastian cumulative-time standings."""
    result: dict[str, Any] = {
        "required_meets": SAINT_SEBASTIAN_REQUIRED_MEETS,
        "seasons": {},
    }
    for season in sorted(df["season_year"].dropna().unique()):
        base = (
            df[df["season_year"] == season]
            .dropna(subset=["finish_time_s", "meet_number", "athlete_full_name"])
            .copy()
        )
        if base.empty:
            continue
        base["meet_number"] = base["meet_number"].astype(int)
        base["division"] = base["division"].fillna("Unknown")
        base["gender"] = base["gender"].fillna("Unknown")
        base["team_name"] = base["team_name"].fillna("Unknown")

        completed_meets = sorted(base["meet_number"].unique())
        standings = (
            base.groupby(["division", "gender", "athlete_full_name", "team_name"])
            .agg(
                cumulative_time_s=("finish_time_s", "sum"),
                meets_run=("meet_number", "nunique"),
            )
            .reset_index()
        )
        standings = standings[standings["meets_run"] == len(completed_meets)].copy()
        standings = standings.sort_values(
            ["division", "gender", "cumulative_time_s", "athlete_full_name"]
        )
        standings["rank"] = standings.groupby(["division", "gender"]).cumcount() + 1
        leader = standings.groupby(["division", "gender"])[
            "cumulative_time_s"
        ].transform("min")
        standings["time_back_s"] = standings["cumulative_time_s"] - leader

        result["seasons"][str(int(season))] = {
            "completed_meets": [int(m) for m in completed_meets],
            "standings": frame_records(standings),
        }
    return result


def build_athlete_histories(df: pd.DataFrame) -> dict[str, Any]:
    """Deterministic representative athlete histories.

    Selection rule (documented so it can be reproduced): the ten athletes
    with the most recorded results (ties broken by name), plus the five
    athletes with the fastest single normalized pace (pace_per_mi_min).
    """
    by_count = (
        df.groupby("athlete_full_name")
        .size()
        .reset_index(name="results")
        .sort_values(["results", "athlete_full_name"], ascending=[False, True])
    )
    most_results = by_count.head(10)["athlete_full_name"].tolist()

    paced = df[df["pace_per_mi_min"].notna() & (df["pace_per_mi_min"] > 0)]
    fastest = (
        paced.sort_values(["pace_per_mi_min", "athlete_full_name"])
        .drop_duplicates("athlete_full_name")
        .head(5)["athlete_full_name"]
        .tolist()
    )

    selected = sorted(set(most_results) | set(fastest))
    history_cols = [
        "athlete_full_name",
        "team_name",
        "season_year",
        "meet_number",
        "division",
        "gender",
        "grade",
        "place_overall",
        "finish_time_str",
        "finish_time_s",
        "pace_per_mi_min",
        "pace_per_mi_str",
        "speed_mph",
    ]
    histories = {}
    for athlete in selected:
        rows = df[df["athlete_full_name"] == athlete].sort_values(
            ["season_year", "meet_number"]
        )
        histories[athlete] = frame_records(rows[history_cols])

    return {
        "selection_rule": (
            "top 10 athletes by number of recorded results (ties broken "
            "alphabetically) union top 5 fastest single pace_per_mi_min"
        ),
        "selected_athletes": selected,
        "histories": histories,
    }


def build_normalized_metrics(df: pd.DataFrame) -> dict[str, Any]:
    distances = (
        df.groupby("division", dropna=False)[["distance_km", "distance_mi"]]
        .agg(["min", "max"])
        .reset_index()
    )
    distance_records = []
    for _, row in distances.iterrows():
        distance_records.append(
            {
                "division": jsonable(row[("division", "")]),
                "distance_km_min": jsonable(row[("distance_km", "min")]),
                "distance_km_max": jsonable(row[("distance_km", "max")]),
                "distance_mi_min": jsonable(row[("distance_mi", "min")]),
                "distance_mi_max": jsonable(row[("distance_mi", "max")]),
            }
        )

    stats = (
        df.groupby(["season_year", "meet_number", "division", "gender"], dropna=False)
        .agg(
            records=("athlete_full_name", "size"),
            finish_time_s_min=("finish_time_s", "min"),
            finish_time_s_mean=("finish_time_s", "mean"),
            finish_time_s_max=("finish_time_s", "max"),
            pace_per_mi_min_best=("pace_per_mi_min", "min"),
            pace_per_mi_min_mean=("pace_per_mi_min", "mean"),
            speed_mph_max=("speed_mph", "max"),
        )
        .reset_index()
        .sort_values(["season_year", "meet_number", "division", "gender"])
    )
    for col in stats.columns:
        if stats[col].dtype == float:
            stats[col] = stats[col].round(4)

    return {
        "division_distances": distance_records,
        "race_group_stats": frame_records(stats),
    }


def write_json(name: str, payload: Any) -> Path:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    path = OUTPUT_DIR / name
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        json.dump(payload, handle, indent=2, ensure_ascii=False, sort_keys=False)
        handle.write("\n")
    return path


def main() -> None:
    df = pd.read_csv(CANONICAL_CSV)

    outputs = {
        "manifest.json": build_manifest(df),
        "counts.json": build_counts(df),
        "team_scores.json": build_team_scores(df),
        "saint_sebastian.json": build_saint_sebastian(df),
        "athlete_histories.json": build_athlete_histories(df),
        "normalized_metrics.json": build_normalized_metrics(df),
    }
    for name, payload in outputs.items():
        path = write_json(name, payload)
        print(f"wrote {path.relative_to(REPO_ROOT).as_posix()}")


if __name__ == "__main__":
    main()
