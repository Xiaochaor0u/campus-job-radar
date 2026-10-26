-- Saved filters for "what appeared today" (岗位订阅).
--
-- One row = one subscription the user keeps.  Conditions inside a rule are
-- AND-ed, values inside one field are OR-ed.  Empty arrays mean "no constraint
-- on this dimension", so "只看实习" does not need a fake keyword.
--
-- The rule never stores results: hits are computed on demand from job_events
-- (event_type='new'), so a rule stays valid as new batches arrive.
--
-- PostgreSQL-specific; SQLite development databases build the same table from
-- the SQLAlchemy metadata (Base.metadata.create_all).

CREATE TABLE watch_rules (
    id VARCHAR(64) PRIMARY KEY,
    name VARCHAR(120) NOT NULL,
    keywords JSONB NOT NULL DEFAULT '[]'::jsonb,
    directions JSONB NOT NULL DEFAULT '[]'::jsonb,
    cities JSONB NOT NULL DEFAULT '[]'::jsonb,
    track VARCHAR(32) NOT NULL DEFAULT '',
    enabled BOOLEAN NOT NULL DEFAULT TRUE,
    created_at TIMESTAMPTZ NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL
);

CREATE INDEX ix_watch_rules_enabled ON watch_rules (enabled);
