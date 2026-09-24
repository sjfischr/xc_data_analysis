-- Migration 0003: historical analytics views (Task 5.3).
--
-- Requirements: R1.3-R1.7, R9.1, R9.3-R9.5, R2.6 ("derive reproducible
-- metrics through versioned queries or views rather than destructive
-- post-processing scripts").
--
-- Every formula here is versioned in metric_versions (seeded below) so a
-- future formula change is a new row, not a silent behavior change under
-- the same name.

CREATE VIEW v_results_enriched AS
WITH base AS (
    SELECT
        r.result_id,
        r.source_id,
        r.source_result_id,
        r.race_id,
        r.athlete_id,
        r.school_id,
        r.ingest_run_id,
        r.finish_time_ms,
        r.original_time_text,
        r.place_overall,
        r.bib,
        r.grade,
        r.scored_flag,
        a.display_name AS athlete_display_name,
        sc.display_name AS school_display_name,
        ra.division_code,
        ra.gender_code,
        ra.distance_meters,
        m.meet_id,
        m.season_year,
        m.meet_number,
        m.series AS meet_series,
        m.name AS meet_name,
        (ra.distance_meters / 1000.0) AS distance_km,
        (r.finish_time_ms / 1000.0) AS finish_time_s
    FROM results r
    JOIN races ra ON ra.race_id = r.race_id
    JOIN meets m ON m.meet_id = ra.meet_id
    JOIN athletes a ON a.athlete_id = r.athlete_id
    JOIN schools sc ON sc.school_id = r.school_id
)
-- The 0.621371 km->mi factor and the (seconds/60)/distance, distance/(seconds/3600)
-- operation order deliberately match add_distance_metrics.py's legacy formula
-- (not the more precise 1/1.609344 factor), so a migrated row and the frozen
-- baseline's own precomputed pace/speed columns agree (Requirement 1.3).
SELECT
    *,
    (distance_km * 0.621371) AS distance_mi,
    CASE WHEN finish_time_s IS NOT NULL
         THEN (finish_time_s / 60.0) / distance_km END AS pace_per_km_min,
    CASE WHEN finish_time_s IS NOT NULL
         THEN (finish_time_s / 60.0) / (distance_km * 0.621371) END AS pace_per_mi_min,
    CASE WHEN finish_time_s IS NOT NULL AND finish_time_s > 0
         THEN distance_km / (finish_time_s / 3600.0) END AS speed_kmh,
    CASE WHEN finish_time_s IS NOT NULL AND finish_time_s > 0
         THEN (distance_km * 0.621371) / (finish_time_s / 3600.0) END AS speed_mph
FROM base;

-- Chronological per-athlete results with the same normalized metrics;
-- callers add their own ORDER BY (a view's internal ordering is not
-- guaranteed once the caller filters or joins further).
CREATE VIEW v_athlete_progression AS
SELECT * FROM v_results_enriched;

-- Team scoring (Requirement 1.4): within one (season, meet, division,
-- gender, school), a school scores only when it has at least 5 finishers
-- with a recorded place; the score is the sum of its best 5 places.
CREATE VIEW v_team_scores AS
WITH ranked AS (
    SELECT
        m.season_year,
        m.meet_number,
        ra.division_code,
        ra.gender_code,
        r.school_id,
        r.place_overall,
        r.finish_time_ms,
        ROW_NUMBER() OVER (
            PARTITION BY m.season_year, m.meet_number, ra.division_code,
                         ra.gender_code, r.school_id
            ORDER BY r.place_overall ASC
        ) AS team_rank,
        COUNT(*) OVER (
            PARTITION BY m.season_year, m.meet_number, ra.division_code,
                         ra.gender_code, r.school_id
        ) AS team_finisher_count
    FROM results r
    JOIN races ra ON ra.race_id = r.race_id
    JOIN meets m ON m.meet_id = ra.meet_id
    JOIN schools sc ON sc.school_id = r.school_id
    WHERE r.place_overall IS NOT NULL
      -- The reserved "no team recorded" placeholder never fields a scoring
      -- team (Requirement 1.7: the gap is explicit, not invented as a team).
      AND sc.canonical_name != 'Unknown'
)
SELECT
    season_year AS season,
    meet_number AS meet,
    division_code AS division,
    gender_code AS gender,
    school_id,
    SUM(place_overall) AS score,
    COUNT(*) AS scoring_runners,
    AVG(finish_time_ms) / 1000.0 AS avg_time_s
FROM ranked
WHERE team_rank <= 5 AND team_finisher_count >= 5
GROUP BY season_year, meet_number, division_code, gender_code, school_id;

-- Saint Sebastian cumulative-time standings (Requirement 1.5). Eligibility
-- is season-relative, not a fixed meet count: an athlete is ranked only if
-- they have a result in every meet_number that produced *any* result that
-- season (so 2023, missing Meet 2 entirely, requires Meets 1+3; 2024/2025
-- require Meets 1+2+3). This mirrors scripts/freeze_baseline.py exactly.
CREATE VIEW v_saint_sebastian AS
WITH season_required_meets AS (
    SELECT m.season_year, COUNT(DISTINCT m.meet_number) AS required_meets
    FROM results r
    JOIN races ra ON ra.race_id = r.race_id
    JOIN meets m ON m.meet_id = ra.meet_id
    WHERE r.finish_time_ms IS NOT NULL
    GROUP BY m.season_year
),
athlete_totals AS (
    SELECT
        m.season_year,
        ra.division_code,
        ra.gender_code,
        r.athlete_id,
        r.school_id,
        SUM(r.finish_time_ms) AS cumulative_time_ms,
        COUNT(DISTINCT m.meet_number) AS meets_run
    FROM results r
    JOIN races ra ON ra.race_id = r.race_id
    JOIN meets m ON m.meet_id = ra.meet_id
    WHERE r.finish_time_ms IS NOT NULL
    GROUP BY m.season_year, ra.division_code, ra.gender_code, r.athlete_id, r.school_id
)
SELECT
    at.season_year,
    at.division_code,
    at.gender_code,
    at.athlete_id,
    a.display_name AS athlete_display_name,
    at.school_id,
    at.cumulative_time_ms,
    at.meets_run,
    srm.required_meets,
    ROW_NUMBER() OVER (
        PARTITION BY at.season_year, at.division_code, at.gender_code
        ORDER BY at.cumulative_time_ms ASC, a.display_name ASC
    ) AS standing_rank,
    at.cumulative_time_ms - MIN(at.cumulative_time_ms) OVER (
        PARTITION BY at.season_year, at.division_code, at.gender_code
    ) AS time_back_ms
FROM athlete_totals at
JOIN season_required_meets srm ON srm.season_year = at.season_year
JOIN athletes a ON a.athlete_id = at.athlete_id
WHERE at.meets_run = srm.required_meets;

-- Within-race and within-season-category percentile rank (Requirement 9.2).
CREATE VIEW v_race_percentiles AS
SELECT
    r.result_id,
    r.race_id,
    r.athlete_id,
    r.finish_time_ms,
    m.season_year,
    ra.division_code,
    ra.gender_code,
    PERCENT_RANK() OVER (
        PARTITION BY r.race_id ORDER BY r.finish_time_ms ASC
    ) AS race_percentile,
    PERCENT_RANK() OVER (
        PARTITION BY m.season_year, ra.division_code, ra.gender_code
        ORDER BY r.finish_time_ms ASC
    ) AS category_percentile
FROM results r
JOIN races ra ON ra.race_id = r.race_id
JOIN meets m ON m.meet_id = ra.meet_id
WHERE r.finish_time_ms IS NOT NULL;

-- Per-race completeness (Requirement 1.7, 9.4): counts and explicit source
-- gaps for every race that exists. A race with no rows at all (for example
-- most 2023 Meet 2 divisions) is not fabricated as a zero-filled row here --
-- its absence from this view, compared against sibling seasons/meets, *is*
-- the documented gap.
CREATE VIEW v_data_completeness AS
SELECT
    m.season_year,
    m.meet_number,
    ra.division_code,
    ra.gender_code,
    ra.race_id,
    COUNT(r.result_id) AS result_count,
    COUNT(DISTINCT r.school_id) AS school_count,
    SUM(CASE WHEN r.place_overall IS NULL THEN 1 ELSE 0 END) AS missing_place_count,
    SUM(CASE WHEN r.finish_time_ms IS NULL THEN 1 ELSE 0 END) AS missing_time_count
FROM races ra
JOIN meets m ON m.meet_id = ra.meet_id
LEFT JOIN results r ON r.race_id = ra.race_id
GROUP BY m.season_year, m.meet_number, ra.division_code, ra.gender_code, ra.race_id;

INSERT INTO metric_versions
    (metric_version_id, metric_name, semantic_version, parameters_json,
     effective_from, implementation_hash, created_at)
VALUES
    (
        'pace_speed:1.0.0',
        'pace_speed',
        '1.0.0',
        '{"distance_mi_factor": 0.621371, "division_distance_meters": {"2nd Grade": 2000, "Frosh": 2000, "JV": 3000, "Varsity": 4000}, "pace_per_km_min": "(finish_time_ms/1000.0/60.0) / distance_km", "pace_per_mi_min": "(finish_time_ms/1000.0/60.0) / distance_mi", "speed_kmh": "distance_km / (finish_time_ms/1000.0/3600.0)", "speed_mph": "distance_mi / (finish_time_ms/1000.0/3600.0)"}',
        '2023-01-01T00:00:00Z',
        'a33afd5f3655be519d62435584270f998195e3673d5463300e29c08551c98a45',
        '2026-09-21T00:00:00Z'
    ),
    (
        'team_score:1.0.0',
        'team_score',
        '1.0.0',
        '{"rule": "sum of the best 5 place_overall values per (season, meet, division, gender, school); a school is excluded if it has fewer than 5 finishers with a recorded place in that race", "scoring_runners": 5, "tiebreak": "none -- lower total score ranks ahead of higher"}',
        '2023-01-01T00:00:00Z',
        '9ee89a31b3c45b08faf46600cf6487621e84abadbdc3d984ab7aee404df534d7',
        '2026-09-21T00:00:00Z'
    ),
    (
        'saint_sebastian:1.0.0',
        'saint_sebastian',
        '1.0.0',
        '{"cumulative_metric": "sum of finish_time_ms across the athlete''s counted meets", "eligibility": "athlete must have a recorded finish_time_ms in every distinct meet_number that produced any result that season", "rank_rule": "ascending cumulative_time_ms, ties broken alphabetically by athlete display_name", "time_back": "cumulative_time_ms minus the leader''s cumulative_time_ms within (season, division, gender)"}',
        '2023-01-01T00:00:00Z',
        '98cf328c9a14f2a104caa366ebfa453f1a5ed51647ed39b887f5d08170d10443',
        '2026-09-21T00:00:00Z'
    );
