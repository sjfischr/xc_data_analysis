-- Migration 0002: canonical domain entities, aliases, resolution, and
-- versioned rule/metric tables.
--
-- Requirements: R2.1-R2.8, R8.6-R8.10
-- Design: design.md section 8 (Relational data model), section 8.1-8.2.
--
-- All identifiers are UUID text. Timestamps are UTC ISO-8601 text. Durations
-- use integer milliseconds and distances use integer meters.

CREATE TABLE meets (
    meet_id      TEXT PRIMARY KEY,
    season_year   INTEGER NOT NULL CHECK (season_year >= 2000),
    meet_number    INTEGER,
    name             TEXT NOT NULL,
    series             TEXT,
    meet_date            TEXT,
    status                 TEXT NOT NULL DEFAULT 'scheduled'
                            CHECK (status IN ('scheduled', 'completed', 'cancelled')),
    venue                    TEXT,
    created_at                 TEXT NOT NULL,
    updated_at                   TEXT NOT NULL
);

CREATE INDEX ix_meets_season ON meets(season_year);

CREATE TABLE races (
    race_id          TEXT PRIMARY KEY,
    meet_id            TEXT NOT NULL REFERENCES meets(meet_id),
    division_code        TEXT NOT NULL,
    gender_code             TEXT NOT NULL CHECK (gender_code IN ('M', 'F', 'X')),
    distance_meters           INTEGER CHECK (distance_meters IS NULL OR distance_meters > 0),
    status                      TEXT NOT NULL DEFAULT 'scheduled'
                                 CHECK (status IN ('scheduled', 'completed', 'cancelled')),
    created_at                    TEXT NOT NULL,
    updated_at                      TEXT NOT NULL
);

CREATE INDEX ix_races_meet ON races(meet_id);
CREATE UNIQUE INDEX ux_races_meet_division_gender ON races(meet_id, division_code, gender_code);

CREATE TABLE schools (
    school_id              TEXT PRIMARY KEY,
    canonical_name           TEXT NOT NULL,
    display_name                TEXT NOT NULL,
    status                        TEXT NOT NULL DEFAULT 'active'
                                   CHECK (status IN ('active', 'merged', 'deleted')),
    merged_into_school_id           TEXT REFERENCES schools(school_id),
    created_at                        TEXT NOT NULL,
    updated_at                           TEXT NOT NULL
);

CREATE UNIQUE INDEX ux_schools_canonical_name ON schools(canonical_name);

CREATE TABLE school_aliases (
    school_alias_id     TEXT PRIMARY KEY,
    school_id              TEXT NOT NULL REFERENCES schools(school_id),
    source_id                 TEXT NOT NULL REFERENCES data_sources(source_id),
    alias_type                   TEXT NOT NULL DEFAULT 'source_name'
                                  CHECK (alias_type IN ('source_name', 'manual')),
    raw_value                       TEXT NOT NULL,
    normalized_value                  TEXT NOT NULL,
    context_key                          TEXT NOT NULL DEFAULT '',
    is_ambiguous                            INTEGER NOT NULL DEFAULT 0 CHECK (is_ambiguous IN (0, 1)),
    created_at                                TEXT NOT NULL
);

CREATE INDEX ix_school_aliases_school ON school_aliases(school_id);
-- Unambiguous aliases are unique within (source, type, normalized value,
-- context); an alias flagged ambiguous is excluded from this constraint
-- because it deliberately has more than one candidate (Requirement 8.5).
CREATE UNIQUE INDEX ux_school_aliases_scope
    ON school_aliases(source_id, alias_type, normalized_value, context_key)
    WHERE is_ambiguous = 0;

CREATE TABLE athletes (
    athlete_id                TEXT PRIMARY KEY,
    canonical_first_name         TEXT NOT NULL,
    canonical_last_name             TEXT NOT NULL,
    display_name                       TEXT NOT NULL,
    status                                TEXT NOT NULL DEFAULT 'active'
                                          CHECK (status IN ('active', 'merged', 'deleted')),
    merged_into_athlete_id                  TEXT REFERENCES athletes(athlete_id),
    created_at                                 TEXT NOT NULL,
    updated_at                                   TEXT NOT NULL
);

CREATE INDEX ix_athletes_name ON athletes(canonical_last_name, canonical_first_name);

CREATE TABLE athlete_aliases (
    athlete_alias_id    TEXT PRIMARY KEY,
    athlete_id              TEXT NOT NULL REFERENCES athletes(athlete_id),
    source_id                  TEXT NOT NULL REFERENCES data_sources(source_id),
    alias_type                    TEXT NOT NULL DEFAULT 'source_name'
                                   CHECK (alias_type IN ('source_name', 'manual')),
    raw_value                        TEXT NOT NULL,
    normalized_value                   TEXT NOT NULL,
    context_key                           TEXT NOT NULL DEFAULT '',
    is_ambiguous                             INTEGER NOT NULL DEFAULT 0 CHECK (is_ambiguous IN (0, 1)),
    created_at                                 TEXT NOT NULL
);

CREATE INDEX ix_athlete_aliases_athlete ON athlete_aliases(athlete_id);
CREATE UNIQUE INDEX ux_athlete_aliases_scope
    ON athlete_aliases(source_id, alias_type, normalized_value, context_key)
    WHERE is_ambiguous = 0;

CREATE TABLE athlete_seasons (
    athlete_season_id   TEXT PRIMARY KEY,
    athlete_id              TEXT NOT NULL REFERENCES athletes(athlete_id),
    season_year                 INTEGER NOT NULL CHECK (season_year >= 2000),
    school_id                      TEXT NOT NULL REFERENCES schools(school_id),
    -- 1-12 covers both the frozen CYO youth league baseline (grades 2-8,
    -- divisions "2nd Grade"/"Frosh"/"JV"/"Varsity") and any future
    -- high-school-only division; it is a sanity bound, not a division rule.
    grade                             INTEGER CHECK (grade IS NULL OR (grade BETWEEN 1 AND 12)),
    gender_code                          TEXT NOT NULL CHECK (gender_code IN ('M', 'F', 'X')),
    evidence_json                           TEXT,
    created_at                                 TEXT NOT NULL,
    updated_at                                    TEXT NOT NULL
);

CREATE INDEX ix_athlete_seasons_athlete ON athlete_seasons(athlete_id);
CREATE INDEX ix_athlete_seasons_school_season ON athlete_seasons(school_id, season_year);
-- Athletes can transfer schools mid-career, so this is season-scoped, not a
-- single immutable athlete->school fact (design.md section 8.1).
CREATE UNIQUE INDEX ux_athlete_seasons ON athlete_seasons(athlete_id, season_year, school_id);

CREATE TABLE results (
    result_id              TEXT PRIMARY KEY,
    source_id                 TEXT NOT NULL REFERENCES data_sources(source_id),
    source_result_id             TEXT,
    race_id                         TEXT NOT NULL REFERENCES races(race_id),
    athlete_id                         TEXT NOT NULL REFERENCES athletes(athlete_id),
    school_id                             TEXT NOT NULL REFERENCES schools(school_id),
    ingest_run_id                            TEXT NOT NULL REFERENCES ingest_runs(ingest_run_id),
    finish_time_ms                              INTEGER CHECK (finish_time_ms IS NULL OR finish_time_ms > 0),
    original_time_text                             TEXT,
    place_overall                                     INTEGER CHECK (place_overall IS NULL OR place_overall > 0),
    bib                                                  TEXT,
    grade                                                   INTEGER CHECK (grade IS NULL OR (grade BETWEEN 1 AND 12)),
    scored_flag                                                TEXT NOT NULL DEFAULT 'unknown'
                                                                CHECK (scored_flag IN ('scored', 'not_scored', 'unknown')),
    -- Non-NULL only for a documented race format where more than one result
    -- per athlete per race is legitimate; see the partial unique index
    -- below (Requirement 8: "one result per athlete/race" constraint).
    multi_result_reason                                          TEXT,
    created_at                                                      TEXT NOT NULL,
    updated_at                                                         TEXT NOT NULL
);

CREATE INDEX ix_results_athlete ON results(athlete_id, created_at);
CREATE INDEX ix_results_race ON results(race_id);
CREATE INDEX ix_results_school_race ON results(school_id, race_id);
CREATE UNIQUE INDEX ux_results_source_result
    ON results(source_id, source_result_id)
    WHERE source_result_id IS NOT NULL;
CREATE UNIQUE INDEX ux_results_one_per_athlete_race
    ON results(race_id, athlete_id)
    WHERE multi_result_reason IS NULL;

CREATE TABLE source_entity_links (
    source_entity_link_id  TEXT PRIMARY KEY,
    source_id                 TEXT NOT NULL REFERENCES data_sources(source_id),
    source_entity_type           TEXT NOT NULL CHECK (source_entity_type IN ('race', 'event', 'result_set')),
    source_entity_id                TEXT NOT NULL,
    meet_id                            TEXT REFERENCES meets(meet_id),
    race_id                               TEXT REFERENCES races(race_id),
    created_at                               TEXT NOT NULL
);

CREATE UNIQUE INDEX ux_source_entity_links
    ON source_entity_links(source_id, source_entity_type, source_entity_id);

CREATE TABLE award_rules (
    award_rule_id       TEXT PRIMARY KEY,
    series                 TEXT NOT NULL,
    season_year_start         INTEGER NOT NULL,
    season_year_end              INTEGER,
    version                         INTEGER NOT NULL CHECK (version > 0),
    config_json                        TEXT NOT NULL,
    created_at                            TEXT NOT NULL
);

CREATE UNIQUE INDEX ux_award_rules_series_version ON award_rules(series, version);

CREATE TABLE metric_versions (
    metric_version_id   TEXT PRIMARY KEY,
    metric_name             TEXT NOT NULL,
    semantic_version           TEXT NOT NULL,
    parameters_json               TEXT NOT NULL,
    effective_from                   TEXT NOT NULL,
    effective_to                        TEXT,
    implementation_hash                    TEXT NOT NULL,
    created_at                                TEXT NOT NULL
);

CREATE UNIQUE INDEX ux_metric_versions ON metric_versions(metric_name, semantic_version);

CREATE TABLE resolution_cases (
    resolution_case_id   TEXT PRIMARY KEY,
    entity_type              TEXT NOT NULL CHECK (entity_type IN ('meet', 'school', 'athlete')),
    ingest_run_id               TEXT REFERENCES ingest_runs(ingest_run_id),
    candidate_entity_id            TEXT,
    evidence_json                     TEXT NOT NULL,
    confidence                           REAL CHECK (confidence IS NULL OR (confidence BETWEEN 0.0 AND 1.0)),
    status                                  TEXT NOT NULL DEFAULT 'pending'
                                             CHECK (status IN ('pending', 'approved', 'rejected', 'split', 'reversed')),
    created_at                                 TEXT NOT NULL,
    updated_at                                    TEXT NOT NULL
);

CREATE INDEX ix_resolution_cases_status ON resolution_cases(status);
CREATE INDEX ix_resolution_cases_entity_type ON resolution_cases(entity_type);

CREATE TABLE resolution_decisions (
    resolution_decision_id  TEXT PRIMARY KEY,
    resolution_case_id         TEXT NOT NULL REFERENCES resolution_cases(resolution_case_id),
    decision_type                  TEXT NOT NULL CHECK (decision_type IN ('approve', 'reject', 'split', 'reverse')),
    actor                              TEXT NOT NULL,
    evidence_summary                      TEXT NOT NULL,
    policy_version                           TEXT,
    affected_records_json                       TEXT NOT NULL,
    reversal_of_decision_id                        TEXT REFERENCES resolution_decisions(resolution_decision_id),
    created_at                                        TEXT NOT NULL
);

CREATE INDEX ix_resolution_decisions_case ON resolution_decisions(resolution_case_id);
