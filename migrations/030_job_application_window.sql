-- Publish date and application deadline, promoted out of the JD prose.
--
-- OfferBiu already ships deadlineAt / deadlineText in its feed, but the first
-- import only rendered them into jd_raw.  Students need both dates next to the
-- title, and "closes soon" needs to be sortable, so they become real columns.
--
-- Nullability is the honest default: many announcements state no deadline at
-- all, and OfferBiu exposes publish time only at day granularity (a batch-level
-- sourceUpdatedAt).  NULL therefore means "the source did not say", and the UI
-- renders it as 「未公布」 rather than inventing a date.
--
-- No backfill here: existing rows keep NULL until a collection pass re-reads
-- them, which is exactly the state we want to show (unknown, not guessed).

ALTER TABLE job_snapshots
    ADD COLUMN IF NOT EXISTS publish_date DATE;

ALTER TABLE job_snapshots
    ADD COLUMN IF NOT EXISTS deadline_date DATE;

ALTER TABLE job_snapshots
    ADD COLUMN IF NOT EXISTS deadline_text VARCHAR(64);

-- "What closed recently / what closes next" is the only query behind these,
-- and it filters on the deadline.
CREATE INDEX IF NOT EXISTS ix_job_snapshots_deadline_date
    ON job_snapshots (deadline_date);

CREATE INDEX IF NOT EXISTS ix_job_snapshots_publish_date
    ON job_snapshots (publish_date);
