-- Intelligence station job event log.
--
-- Purpose: turn consecutive crawls into an auditable change feed so students
-- can answer "which companies posted something new since I last looked".
--
-- Design notes:
--
--   * ``baseline`` marks the first complete crawl of a company. On that run
--     every job is "new" by definition, which would flood the digest, so the
--     writer records a single baseline marker row instead and the digest
--     queries exclude the type entirely.
--   * ``content_fingerprint`` de-duplicates repeated ``updated`` events: a
--     re-crawl that observes the same fingerprint again writes nothing.
--   * The unique constraint (run_id, job_id, event_type) keeps a re-run of
--     the same scheduler run idempotent.
--
-- This migration is PostgreSQL-specific; SQLite development databases build
-- the same table from the SQLAlchemy metadata (Base.metadata.create_all).

CREATE TABLE job_events (
    id VARCHAR(64) PRIMARY KEY,
    event_type VARCHAR(16) NOT NULL,
    job_id VARCHAR(255) NOT NULL,
    company_id VARCHAR(255) NOT NULL,
    company_name VARCHAR(255) NOT NULL,
    title VARCHAR(512) NOT NULL,
    city VARCHAR(255),
    batch VARCHAR(64) NOT NULL,
    track VARCHAR(32) NOT NULL DEFAULT 'unknown',
    detail_url VARCHAR(2048) NOT NULL,
    content_fingerprint VARCHAR(64),
    detected_at TIMESTAMPTZ NOT NULL,
    run_id VARCHAR(255) NOT NULL,
    created_at TIMESTAMPTZ NOT NULL,
    CONSTRAINT ck_job_events_event_type
        CHECK (event_type IN ('new', 'updated', 'removed', 'baseline'))
);

CREATE UNIQUE INDEX uq_job_events_run_job_type
    ON job_events (run_id, job_id, event_type);

CREATE INDEX ix_job_events_detected_at
    ON job_events (detected_at);

CREATE INDEX ix_job_events_company_detected
    ON job_events (company_id, detected_at);

CREATE INDEX ix_job_events_type_detected
    ON job_events (event_type, detected_at);
