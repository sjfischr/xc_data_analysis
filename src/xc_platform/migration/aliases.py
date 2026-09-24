"""Seed approved school and athlete decisions (Task 5.2).

Two legacy curation artifacts already shaped the frozen canonical CSV before
it was frozen:

* ``standardize_team_names.py`` collapsed raw-source team-name spelling
  variants (e.g. ``"St. Agnes Parish"``) into the standardized names
  ``data/merged/season_results.csv`` now stores directly (e.g. ``"St
  Agnes"``). The correction is already baked into the results; this module
  additionally seeds the *mapping itself* as ``school_aliases`` rows so the
  historical decision has provenance, and so a raw source page using the old
  spelling would route correctly if it were ever reprocessed.
* ``name_corrections.csv`` records per-athlete curation actions. ``apply``
  rows are likewise already baked into ``athlete_full_name`` and are seeded
  as ``athlete_aliases``; ``review`` rows stay open resolution cases;
  ``keep`` rows are recorded as an already-decided "no correction needed"
  case (Requirement 8.9's "confirmed-distinct pairs stay separate"). No
  ambiguous pair becomes an automatic alias (Task 5.2's explicit bullet).
"""

from __future__ import annotations

import csv
import json
import re
from dataclasses import dataclass
from pathlib import Path

from xc_platform.db.identifiers import new_id, utc_now_iso
from xc_platform.migration.canonical_writer import HistoricalCanonicalWriter
from xc_platform.migration.historical_csv import HistoricalRow

# Verbatim copy of standardize_team_names.py's team_name_mapping. This
# mapping is itself part of the frozen historical record (Requirement 1.8):
# it describes a decision already applied to the 2023-2025 baseline, so it
# is copied here rather than imported (the source script executes a full
# read-modify-write of season_results.csv as a side effect of import, which
# would violate "read the frozen canonical CSV without modifying it").
LEGACY_TEAM_NAME_MAPPING: dict[str, str] = {
    "St. Agnes Parish": "St Agnes",
    "St. Ambrose Parish": "St Ambrose",
    "St. Ann Parish": "St Ann",
    "St. Anthony of Padua Parish": "St Anthony",
    "St. Bernadette Parish": "St Bernadette",
    "St. Francis of Assisi Parish": "St Francis",
    "St. James Parish": "St James",
    "St. John The Evangelist Parish": "St John the Evangelist",
    "St. John the Evangelist Parish": "St John the Evangelist",
    "St. Joseph Parish": "St Joseph",
    "St. Louis Parish": "St Louis",
    "St. Mark Parish": "St Mark",
    "St. Michael Parish": "St Michael",
    "St. Rita Parish Alexandria": "St Rita",
    "St. Theresa Parish": "St Theresa",
    "St. Thomas More Caedral Parish": "St Thomas More",
    "St. Thomas More Cathedral Parish": "St Thomas More",
    "St. Veronica Parish": "St Veronica",
    "Basilica of Saint Mary Parish": "Basilica of St Mary",
    "Blessed Sacrament Parish": "Blessed Sacrament",
    "Holy Family Parish": "Holy Family",
    "Holy Spirit Parish": "Holy Spirit",
    "Our Lady of Hope Parish": "OLOH",
    "Queen of Apostles Parish": "Q of A",
}


def normalize_alias_value(value: str) -> str:
    """Case/whitespace-fold an alias raw value for lookup matching."""
    return re.sub(r"\s+", " ", value.strip()).lower()


def seed_school_aliases(writer: HistoricalCanonicalWriter, *, source_id: str) -> int:
    """Seed ``school_aliases`` from the legacy team-name mapping. Returns count."""
    conn = writer.connection
    now = utc_now_iso()
    created = 0
    for raw_value, canonical_name in LEGACY_TEAM_NAME_MAPPING.items():
        school_id = writer.get_or_create_school(canonical_name)
        normalized = normalize_alias_value(raw_value)
        existing = conn.execute(
            "SELECT school_alias_id FROM school_aliases WHERE source_id = ? "
            "AND alias_type = 'source_name' AND normalized_value = ? "
            "AND context_key = ''",
            (source_id, normalized),
        ).fetchone()
        if existing is not None:
            continue
        with writer.transaction() as tx:
            tx.execute(
                "INSERT INTO school_aliases (school_alias_id, school_id, "
                "source_id, alias_type, raw_value, normalized_value, "
                "context_key, created_at) "
                "VALUES (?, ?, ?, 'source_name', ?, ?, '', ?)",
                (new_id(), school_id, source_id, raw_value, normalized, now),
            )
        created += 1
    return created


@dataclass(frozen=True, slots=True)
class NameCorrectionsSummary:
    applied_aliases: int
    review_cases: int
    keep_decisions: int


def seed_name_corrections(
    writer: HistoricalCanonicalWriter,
    corrections_csv_path: str | Path,
    *,
    source_id: str,
) -> NameCorrectionsSummary:
    """Seed athlete_aliases and resolution cases from ``name_corrections.csv``."""
    from xc_platform.db.repositories.resolution import ResolutionRepository

    conn = writer.connection
    resolution = ResolutionRepository(conn)
    now = utc_now_iso()

    applied = 0
    reviewed = 0
    kept = 0

    with Path(corrections_csv_path).open("r", encoding="utf-8", newline="") as handle:
        for row in csv.DictReader(handle):
            action = row["action"].strip()
            original_name = row["original_name"].strip()
            corrected_name = row["corrected_name"].strip()
            team = row["team"].strip()
            notes = row.get("notes", "").strip()

            if action == "apply":
                athlete_id = writer.get_or_create_athlete(corrected_name)
                normalized = normalize_alias_value(original_name)
                existing = conn.execute(
                    "SELECT athlete_alias_id FROM athlete_aliases WHERE "
                    "source_id = ? AND alias_type = 'manual' AND "
                    "normalized_value = ? AND context_key = ?",
                    (source_id, normalized, team),
                ).fetchone()
                if existing is None:
                    with writer.transaction() as tx:
                        tx.execute(
                            "INSERT INTO athlete_aliases (athlete_alias_id, "
                            "athlete_id, source_id, alias_type, raw_value, "
                            "normalized_value, context_key, created_at) "
                            "VALUES (?, ?, ?, 'manual', ?, ?, ?, ?)",
                            (
                                new_id(),
                                athlete_id,
                                source_id,
                                original_name,
                                normalized,
                                team,
                                now,
                            ),
                        )
                    applied += 1
            elif action == "review":
                resolution.open_case(
                    entity_type="athlete",
                    evidence_json=json.dumps(
                        {
                            "original_name": original_name,
                            "corrected_name": corrected_name,
                            "team": team,
                            "notes": notes,
                        }
                    ),
                )
                reviewed += 1
            elif action == "keep":
                case_id = resolution.open_case(
                    entity_type="athlete",
                    evidence_json=json.dumps(
                        {"original_name": original_name, "team": team, "notes": notes}
                    ),
                )
                resolution.record_decision(
                    resolution_case_id=case_id,
                    decision_type="reject",
                    actor="migration:name_corrections.csv",
                    evidence_summary=(
                        f"Reviewed and confirmed correct as written: {notes}"
                        if notes
                        else "Reviewed and confirmed correct as written."
                    ),
                    affected_records_json=json.dumps([f"athlete_name:{original_name}"]),
                )
                kept += 1
            else:
                raise ValueError(f"Unknown name_corrections.csv action: {action!r}")

    return NameCorrectionsSummary(
        applied_aliases=applied, review_cases=reviewed, keep_decisions=kept
    )


def resolve_team_name(writer: HistoricalCanonicalWriter, row: HistoricalRow) -> str:
    """Return the canonical school_id for a row's team, or the Unknown placeholder."""
    if row.team_name is None:
        return writer.get_unknown_school()
    return writer.get_or_create_school(row.team_name)
