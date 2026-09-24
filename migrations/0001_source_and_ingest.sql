-- Migration 0001: source registry, ingest run tracking, staging, and SQLite
-- publication lineage.
--
-- Requirements: R2.1-R2.6, R7, R18.1-R18.2
-- Design: design.md section 8 (Relational data model), section 8.1 (Core
-- tables), section 8.2 (Constraints and indexes).
--
-- Content hashes and S3 references are stored here instead of raw payload
-- bytes (Task 4.2): source_objects.raw_s3_key points at the immutable S3
-- object; SQLite never duplicates the payload itself.

CREATE TABLE data_sources (
    source_id        TEXT PRIMARY KEY,
    source_namespace TEXT NOT NULL,
    adapter_type     TEXT NOT NULL
                      CHECK (adapter_type IN ('runsignup', 'tavily_discovery', 'historical_csv')),
    base_domain      TEXT NOT NULL,
    status           TEXT NOT NULL DEFAULT 'active'
                      CHECK (status IN ('active', 'disabled')),
    created_at       TEXT NOT NULL
);

CREATE UNIQUE INDEX ux_data_sources_namespace ON data_sources(source_namespace);

CREATE TABLE db_publications (
    publication_id         TEXT PRIMARY KEY,
    parent_publication_id  TEXT REFERENCES db_publications(publication_id),
    snapshot_key            TEXT NOT NULL,
    snapshot_version_id      TEXT NOT NULL,
    content_sha256            TEXT NOT NULL,
    byte_size                  INTEGER NOT NULL CHECK (byte_size > 0),
    schema_version               INTEGER NOT NULL,
    status                        TEXT NOT NULL DEFAULT 'active'
                                   CHECK (status IN ('active', 'superseded', 'orphaned')),
    created_at                     TEXT NOT NULL,
    created_by_ingest_run_id        TEXT REFERENCES ingest_runs(ingest_run_id),
    summary_json                     TEXT NOT NULL
);

CREATE INDEX ix_db_publications_status ON db_publications(status);
CREATE INDEX ix_db_publications_parent ON db_publications(parent_publication_id);

-- ingest_runs and db_publications reference each other (an ingest run
-- produces a publication; a publication records which run created it).
-- SQLite resolves forward references inside a single migration script at
-- DML time, not CREATE TABLE time, so the declaration order here is safe.
CREATE TABLE ingest_runs (
    ingest_run_id          TEXT PRIMARY KEY,
    source_id               TEXT NOT NULL REFERENCES data_sources(source_id),
    submitted_url             TEXT NOT NULL,
    requested_scope_json       TEXT,
    adapter_type                 TEXT NOT NULL
                                  CHECK (adapter_type IN ('runsignup', 'tavily_discovery', 'historical_csv')),
    state                          TEXT NOT NULL DEFAULT 'pending'
                                    CHECK (state IN (
                                        'pending', 'discovering', 'staging',
                                        'awaiting_review', 'committing',
                                        'committed', 'failed', 'rolled_back'
                                    )),
    inserted_count                   INTEGER NOT NULL DEFAULT 0 CHECK (inserted_count >= 0),
    updated_count                      INTEGER NOT NULL DEFAULT 0 CHECK (updated_count >= 0),
    unchanged_count                      INTEGER NOT NULL DEFAULT 0 CHECK (unchanged_count >= 0),
    quarantined_count                      INTEGER NOT NULL DEFAULT 0 CHECK (quarantined_count >= 0),
    requests_used                            INTEGER NOT NULL DEFAULT 0 CHECK (requests_used >= 0),
    tavily_credits_used                        INTEGER NOT NULL DEFAULT 0 CHECK (tavily_credits_used >= 0),
    started_at                                   TEXT NOT NULL,
    finished_at                                    TEXT,
    parent_publication_id                            TEXT REFERENCES db_publications(publication_id),
    result_publication_id                              TEXT REFERENCES db_publications(publication_id),
    error_summary                                        TEXT,
    correlation_id                                         TEXT NOT NULL
);

CREATE INDEX ix_ingest_runs_source ON ingest_runs(source_id);
CREATE INDEX ix_ingest_runs_state ON ingest_runs(state);
CREATE INDEX ix_ingest_runs_correlation ON ingest_runs(correlation_id);

CREATE TABLE source_objects (
    source_object_id     TEXT PRIMARY KEY,
    source_id              TEXT NOT NULL REFERENCES data_sources(source_id),
    ingest_run_id            TEXT NOT NULL REFERENCES ingest_runs(ingest_run_id),
    source_url                 TEXT NOT NULL,
    raw_s3_key                   TEXT NOT NULL,
    content_sha256                 TEXT NOT NULL,
    byte_size                        INTEGER NOT NULL CHECK (byte_size >= 0),
    media_type                         TEXT NOT NULL,
    extraction_strategy                  TEXT NOT NULL
                                          CHECK (extraction_strategy IN ('runsignup_rest', 'tavily_discovery', 'historical_csv')),
    retrieved_at                           TEXT NOT NULL,
    request_id                               TEXT
);

CREATE INDEX ix_source_objects_ingest_run ON source_objects(ingest_run_id);
-- Idempotency: the same immutable payload (by content hash) is never stored
-- twice for a given source, so re-importing an unchanged source detects
-- "unchanged" without a second S3 fetch (Requirement 4.6, 7.4-7.5).
CREATE UNIQUE INDEX ux_source_objects_content ON source_objects(source_id, content_sha256);

CREATE TABLE staged_results (
    staged_result_id       TEXT PRIMARY KEY,
    ingest_run_id             TEXT NOT NULL REFERENCES ingest_runs(ingest_run_id),
    source_object_id            TEXT NOT NULL REFERENCES source_objects(source_object_id),
    source_id                     TEXT NOT NULL REFERENCES data_sources(source_id),
    source_result_id                TEXT,
    idempotency_key                    TEXT NOT NULL,
    raw_fields_json                      TEXT NOT NULL,
    candidate_fields_json                  TEXT NOT NULL,
    validation_state                         TEXT NOT NULL DEFAULT 'pending'
                                              CHECK (validation_state IN (
                                                  'pending', 'valid', 'quarantined',
                                                  'superseded', 'committed'
                                              )),
    validation_warnings_json                   TEXT,
    -- Advisory pointer to a canonical results row this staged record
    -- conflicts with (Requirement 7.6). Deliberately not a foreign key: the
    -- results table is created in migration 0002, and a conflict may point
    -- at a result that is later superseded without cascading that change
    -- back onto the immutable staging audit trail.
    conflicts_with_result_id                     TEXT,
    created_at                                     TEXT NOT NULL
);

CREATE INDEX ix_staged_results_ingest_run ON staged_results(ingest_run_id);
CREATE INDEX ix_staged_results_idempotency ON staged_results(source_id, idempotency_key);
CREATE INDEX ix_staged_results_validation_state ON staged_results(validation_state);
