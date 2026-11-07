-- Each aggregate table's fingerprint as of the last successful analyze run, per
-- agency: its logic version (pipeline.agg_registry), the plausibility clamp,
-- the ingest strategy, and for a table that reads the timetable, the loaded
-- timetable's content. A run finding a table's fingerprint changed or absent
-- rebuilds that table in full, along with the tables computed from it; one
-- finding it unchanged leaves the table to its incremental rule
-- (pipeline.agg_registry.table_scopes). built_at is when a run last rewrote
-- the table's rows; a run that leaves the table standing keeps it. Written in
-- the run's transaction, so a failed run leaves the previous fingerprints and
-- retries.
CREATE TABLE IF NOT EXISTS agg_table_state (
    agency_id   INT         NOT NULL REFERENCES agencies(agency_id) ON DELETE CASCADE,
    table_name  TEXT        NOT NULL,
    fingerprint TEXT        NOT NULL,
    built_at    TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    PRIMARY KEY (agency_id, table_name)
);
