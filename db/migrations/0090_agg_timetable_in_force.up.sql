-- The timetable versions in force over each run of service dates as the
-- agency's aggregates were last built (pipeline.timetable_in_force), with each
-- version's content. The next analyze rebuilds the dates whose version or
-- content in force differs from what it finds now: a timetable loaded after
-- days it covers were built, or a span recorded for one. Written in the run's
-- transaction, so a failed run leaves the previous rows and retries.
CREATE TABLE IF NOT EXISTS agg_timetable_in_force (
    agency_id         INT  NOT NULL REFERENCES agencies(agency_id) ON DELETE CASCADE,
    first_day         DATE NOT NULL,
    last_day          DATE NOT NULL,
    static_version_id TEXT NOT NULL,
    content_hash      TEXT NOT NULL,
    PRIMARY KEY (agency_id, first_day)
);
