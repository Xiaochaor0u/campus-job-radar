-- InternOps internship classification metadata (TASK-002b).
--
-- Nullability is a business decision, not an oversight:
--
--   * internship_seasons, internship_requirements and match_conditions stay
--     NULL for rows the internship pipeline never analysed.  Defaulting them to
--     '[]' / '{}' would assert a completed analysis that never happened, and the
--     difference between "not analysed" and "analysed, nothing found" cannot be
--     recovered afterwards.
--   * track and conversion_opportunity use 'unknown' rather than NULL because
--     'unknown' already means "no evidence", which is also the correct answer
--     for a legacy row.  There is no second state for it to be confused with.
--
-- Existing rows are not backfilled: guessing a track from a campus-era row would
-- fabricate classification evidence.  They keep track='unknown' and NULL JSON.

ALTER TABLE job_snapshots
    ADD COLUMN track VARCHAR(32) NOT NULL DEFAULT 'unknown';

ALTER TABLE job_snapshots
    ADD COLUMN internship_seasons JSONB;

ALTER TABLE job_snapshots
    ADD COLUMN conversion_opportunity VARCHAR(16) NOT NULL DEFAULT 'unknown';

ALTER TABLE job_snapshots
    ADD COLUMN internship_requirements JSONB;

ALTER TABLE job_snapshots
    ADD COLUMN match_conditions JSONB;

ALTER TABLE job_snapshots
    ADD CONSTRAINT ck_job_snapshots_track
    CHECK (track IN ('campus', 'internship', 'social', 'unknown'));

ALTER TABLE job_snapshots
    ADD CONSTRAINT ck_job_snapshots_conversion_opportunity
    CHECK (conversion_opportunity IN ('yes', 'no', 'unknown'));

-- A CHECK constraint cannot contain a subquery, so the seasons validation lives
-- in an IMMUTABLE helper.  It enforces three things at once: the value is an
-- array, every element is an allowed season label, and no element repeats.
--
-- 'unspecified' is intentionally absent: an internship whose JD names no season
-- is stored as '[]', not as a member of the array.
CREATE OR REPLACE FUNCTION recruitops_valid_internship_seasons(value JSONB)
RETURNS BOOLEAN
LANGUAGE sql
IMMUTABLE
AS $fn$
    SELECT value IS NULL OR (
        jsonb_typeof(value) = 'array'
        AND value <@ '["summer", "winter", "daily"]'::jsonb
        AND jsonb_array_length(value) = (
            SELECT count(DISTINCT season)
            FROM jsonb_array_elements_text(value) AS element(season)
        )
    );
$fn$;

ALTER TABLE job_snapshots
    ADD CONSTRAINT ck_job_snapshots_internship_seasons
    CHECK (recruitops_valid_internship_seasons(internship_seasons));

-- Only 'track' is indexed: every internship list query filters on it.  The JSON
-- columns are display evidence and have no query behind them yet, so adding a
-- GIN index now would be speculative.
CREATE INDEX IF NOT EXISTS ix_job_snapshots_track
    ON job_snapshots (track);
