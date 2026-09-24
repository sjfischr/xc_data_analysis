---
name: xc-domain-data-gaps
description: 'Use when handling XC dataset gaps or quality history: Meet 2 gaps, 2023 fixes, mojibake corrections, duplicate names, the frozen baseline as source of truth, and the never-synthesize-results rule.'
---

# Known data gaps and how to treat them

> Status: **normative** — these gaps are frozen into the Task 1.4 baseline
> and must never be papered over.

## The cardinal rule

**Never synthesize, interpolate, or guess race results.** A missing result
stays missing, is reported as missing (`v_data_completeness`), and is
explained in the UI. This is youth athletics data; fabricated results are the
worst possible failure.

## Known gaps and quality history

| Item | Detail | Reference |
|------|--------|-----------|
| Meet 2 athlete-level data (2023, 2024) | Athlete-level availability unverified from current saved sources; feasibility gate **F2** decides whether RunSignup/Tavily can recover it. Until then it is a documented gap. | `MEET2_DATA_GAP_ANALYSIS.md` |
| 2023 data fixes | Corrections applied to 2023 results; provenance in report. | `2023_DATA_FIX_REPORT.md` |
| Mojibake names | Encoding-damaged names were flagged and corrected; mapping preserved. | `data/merged/mojibake_names.csv`, `data/merged/name_mapping.csv` |
| Duplicate names | Manual merge/correction history; regression set for resolution. | `DUPLICATE_NAMES_REPORT.md`, `name_corrections.csv` |
| Stale docs | `DATASET_COLUMNS.md` cites 3,684 records / 1,417 athletes; the frozen canonical CSV actually has **4,204 records / 1,440 athletes / 27 teams**. Trust the baseline manifest. | `tests/fixtures/baseline/manifest.json` |

## Working rules

1. The canonical baseline is `data/merged/season_results.csv` as fingerprinted
   in `tests/fixtures/baseline/manifest.json` (SHA-256, counts, schema). Any
   other `season_results_*.csv` is an intermediate artifact — do not ingest.
2. Data-changing work must preserve source provenance and provide a rollback
   path (spec execution rule).
3. Completeness is a first-class query: surfaces missing meets/races per
   season rather than hiding them.
4. When sources disagree, record both with provenance and open a resolution
   case — do not pick a winner silently.
