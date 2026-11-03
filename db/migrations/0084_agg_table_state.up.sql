-- Each aggregate table's fingerprint as of the analyze run that last built it,
-- per agency: its logic version (pipeline.agg_registry), the plausibility
-- clamp, the ingest strategy, and for a table that reads the timetable, the
-- loaded timetable's content. A run finding a table's fingerprint changed
-- rebuilds that table in full; one finding it unchanged rebuilds only the
-- dates the ledger (agg_feed_health) reports as changed. Written in the run's
-- transaction, so a failed run leaves the previous fingerprints and retries.
CREATE TABLE IF NOT EXISTS agg_table_state (
    agency_id   INT         NOT NULL REFERENCES agencies(agency_id) ON DELETE CASCADE,
    table_name  TEXT        NOT NULL,
    fingerprint TEXT        NOT NULL,
    built_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (agency_id, table_name)
);
