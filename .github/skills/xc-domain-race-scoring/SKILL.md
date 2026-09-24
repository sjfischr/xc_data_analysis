---
name: xc-domain-race-scoring
description: 'Use for XC domain questions: meet/race/result hierarchy, division distances, integer ms/meters units, top-five team scoring, and Saint Sebastian standings rules for the NVJCYO platform.'
---

# Race hierarchy, distances, and scoring

> Status: **normative domain rules** (source: spec design.md §8 and the
> frozen baseline in `tests/fixtures/baseline/`).

## Hierarchy

```
MEET (season_year, meet_number, name, series, date, status, venue)
 └─ RACE (division_code, gender_code, distance_meters, status)
     └─ RESULT (athlete, school, official place, time)
```

- Seasons 2023–2025 exist today, three scored meets per season (plus a
  developmental series).
- **Units are integers**: durations in **milliseconds**, distances in
  **meters**. Never floats, never "minutes" columns in the database.
- Each RACE carries its own source-reported distance; observed division
  distances in the current data: 2nd Grade 2 km, Frosh 2 km, JV 3 km,
  Varsity 4 km. Treat the per-race value as authoritative, not the table.

## Team scoring (`v_team_scores`)

1. Within one race (season, meet, division, gender), take each school's
   finishers ordered by **official overall place**.
2. A school **scores only with ≥ 5 finishers**; its score is the **sum of its
   first five places**. Lower is better.
3. When the source publishes a "scored" flag, use it to validate — a mismatch
   is a data-quality finding, not something to silently reconcile.
4. Displaced runners (6th, 7th) count toward opponents' places exactly as the
   official places say — do not re-rank or re-number finishers.

## Saint Sebastian standings (`v_saint_sebastian`)

- Per season, per division+gender: an athlete is eligible only after
  completing **every completed meet** of that season.
- Rank eligible athletes by **cumulative finish time** (sum across the
  season's completed meets); report meets completed, rank, and time-back to
  the leader.
- Scoring parameters come from **versioned `award_rules`** so rule tweaks
  never rewrite history.

The frozen baseline (`tests/fixtures/baseline/team_scores.json`,
`saint_sebastian.json`) is the parity target: the new platform must
reproduce those numbers from the same inputs before cutover.

## Anti-patterns

- Recomputing places from times (ties and official adjudications exist).
- Scoring a 4-finisher team "partially".
- Averaging away distance differences across divisions when comparing paces —
  normalize explicitly and say so.
