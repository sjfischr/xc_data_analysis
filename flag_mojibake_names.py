"""Scan merged season results for likely mojibake names.

This helper is meant to isolate athlete names with common mojibake byte
sequences (e.g., the UTF-8 replacement sequence rendered as 'ï¿½').  It
produces a CSV with one row per suspicious athlete spelling so the staff
can decide which entries need manual corrections.
"""
from __future__ import annotations

from collections import Counter
from pathlib import Path
from typing import Iterable, Sequence

import pandas as pd

DATA_PATH = Path("data/merged/season_results.csv")
OUTPUT_PATH = Path("data/merged/mojibake_names.csv")

# Common byte-sequence artifacts we have observed in the raw HTML files.
SUSPECT_PATTERNS: Sequence[str] = (
    "ï¿½",  # UTF-8 replacement character rendered with Latin-1 decoding
    "Ã",
    "Â",
)


def _find_patterns(value: str) -> Iterable[str]:
    """Yield suspect patterns found in *value*."""
    for token in SUSPECT_PATTERNS:
        if token in value:
            yield token


def main() -> None:
    if not DATA_PATH.exists():
        raise SystemExit(f"Missing merged dataset at {DATA_PATH}")

    df = pd.read_csv(DATA_PATH)
    name_col = "athlete_full_name"
    if name_col not in df.columns:
        raise SystemExit(f"Expected column '{name_col}' not found in dataset")

    suspect_rows = []
    for name, rows in df.groupby(name_col):
        if not isinstance(name, str):
            continue
        tokens = tuple(_find_patterns(name))
        if not tokens:
            continue

        teams = sorted({str(v) for v in rows["team_name"].dropna().unique()})
        seasons = sorted({int(v) for v in rows["season_year"].dropna().unique()})
        meets = sorted({str(v) for v in rows["meet_name"].dropna().unique()})
        suspect_rows.append(
            {
                "athlete_full_name": name,
                "occurrences": len(rows),
                "patterns": " ".join(tokens),
                "teams": "; ".join(teams),
                "seasons": "; ".join(map(str, seasons)),
                "meets": "; ".join(meets),
            }
        )

    if not suspect_rows:
        print("No suspect names detected. Nothing written.")
        return

    suspect_df = pd.DataFrame(suspect_rows).sort_values(
        by=["patterns", "athlete_full_name"],
        key=lambda col: col.str.lower() if col.dtype == "object" else col,
    )
    suspect_df.to_csv(OUTPUT_PATH, index=False)

    pattern_counts = Counter(
        token for entry in suspect_rows for token in entry["patterns"].split()
    )

    print(f"Wrote {len(suspect_df)} suspect name(s) to {OUTPUT_PATH}")
    print("Pattern tally:")
    for token, count in pattern_counts.most_common():
        print(f"  {token!r}: {count}")


if __name__ == "__main__":
    main()
