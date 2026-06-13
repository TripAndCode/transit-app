-- Per-route, per-day delay summary — powers the fast 最新観測 / today route-summary
-- path. Built by pipeline/analyze.py from the deduped latest-observation-per-stop
-- rows, so the endpoint reads a tiny table (routes × days × service) and never
-- scans raw `updates` (which the planner mis-estimates for a single agency's
-- latest day when other agencies concentrate on the same dates).
CREATE TABLE IF NOT EXISTS agg_route_daily (
    agency_id       INTEGER NOT NULL REFERENCES agencies(agency_id),
    date            DATE    NOT NULL,
    route_code      TEXT    NOT NULL,
    service_type    TEXT    NOT NULL,
    avg_delay_sec   INTEGER NOT NULL,
    worst_delay_sec INTEGER NOT NULL,
    trips_observed  INTEGER NOT NULL,
    samples         INTEGER NOT NULL,
    last_seen_at    TIMESTAMPTZ NOT NULL,
    -- The (agency_id, date) PK prefix serves both the endpoint's MAX(date)
    -- lookup (backward index-only scan) and the day's row fetch, so no
    -- secondary index is needed.
    PRIMARY KEY (agency_id, date, route_code, service_type)
);
