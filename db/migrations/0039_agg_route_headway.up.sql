-- Scheduled-headway classification (`agg_route_headway`) and reconstructed
-- actual-headway summary (`agg_route_headway_daily`), both populated by
-- `pipeline.analyze.analyze()`. See `pipeline/headways.py` for the shared
-- reconstruction/classification logic both builders use.
--
-- `agg_route_headway` is one row per (agency, route) derived purely from the
-- static GTFS schedule (`static_stop_times`/`static_trips`/`static_routes`),
-- independent of any RT history -- a route absent from this table has no
-- resolvable static schedule for its route_code and must be treated as
-- unclassified, never as "not high-frequency".
CREATE TABLE IF NOT EXISTS agg_route_headway (
    agency_id                     INTEGER NOT NULL REFERENCES agencies(agency_id),
    route_code                    TEXT    NOT NULL,
    scheduled_headway_median_sec  REAL    NOT NULL,
    scheduled_samples             INTEGER NOT NULL,
    is_high_frequency             BOOLEAN NOT NULL,
    PRIMARY KEY (agency_id, route_code)
);

-- `agg_route_headway_daily` is one row per (agency, route, service day),
-- reconstructed from GTFS-RT observations grouped by the physical stop
-- (`updates.stop_id`). Only populated for an agency whose ingest strategy is
-- confirmed to populate `stop_id` (today: `static_join`) -- an agency on any
-- other ingest_strategy is skipped entirely (zero rows here), same
-- "row presence keyed off ingest_strategy, not a sentinel value" convention
-- as `agg_service_delivered_daily`. A day with fewer than two same-stop,
-- same-route observations has no row at all (no gap to measure).
CREATE TABLE IF NOT EXISTS agg_route_headway_daily (
    agency_id                  INTEGER NOT NULL REFERENCES agencies(agency_id),
    route_code                 TEXT    NOT NULL,
    date                       DATE    NOT NULL,
    actual_headway_median_sec  REAL    NOT NULL,
    actual_samples             INTEGER NOT NULL,
    PRIMARY KEY (agency_id, route_code, date)
);
