-- Per-day dominant `static_version_id` seen in ClickHouse `updates`,
-- powering schedule-revision-boundary markers on time-series views so a
-- metric change that coincides with a timetable revision isn't misread as
-- a service-quality change.
--
-- `static_version_id` is the version stamped on the MOST rows that day
-- (mode, not latest-observation) -- a reload happening mid-day produces a
-- brief mix of both versions' rows, and the day's headline version is
-- whichever one actually served most of that day's service, not whichever
-- happened to be seen last.
--
-- A day with no non-NULL `static_version_id` anywhere in `updates` (e.g.
-- Aomori, whose ingest strategy never joins static data at all, or any day
-- predating item 88's rollout) has NO ROW here at all, never a NULL-version
-- row -- the read path (`pipeline.reports.schedule_revision`) treats a
-- missing day as "unknown", and deliberately does not report a boundary
-- against an unknown neighbor.
CREATE TABLE IF NOT EXISTS agg_schedule_revision_daily (
    agency_id         INTEGER NOT NULL REFERENCES agencies(agency_id),
    date              DATE    NOT NULL,
    static_version_id TEXT    NOT NULL,
    -- The (agency_id, date) PK prefix serves the sargable range scan
    -- (WHERE agency_id=? AND date BETWEEN ? AND ?), so no secondary index.
    PRIMARY KEY (agency_id, date)
);
