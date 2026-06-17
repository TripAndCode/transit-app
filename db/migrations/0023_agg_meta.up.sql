-- Audit record of the last analyze() run per agency. NOT load-bearing: the
-- freshness gate (pipeline/freshness.py) derives staleness from the agg tables
-- themselves, so this table needs no backfill. Its sole purpose is the forensic
-- "when was this agency last built / is it pre- or post- a given logic change"
-- question (e.g. the JST captured_at::date fix, PR #77).
CREATE TABLE IF NOT EXISTS agg_meta (
    agency_id               INTEGER     NOT NULL REFERENCES agencies(agency_id),
    analyzed_at             TIMESTAMPTZ NOT NULL,
    max_updates_captured_at TIMESTAMPTZ,
    PRIMARY KEY (agency_id)
);
