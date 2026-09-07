-- Per-day count of non-executed trips (trip-level CANCELED or any stop-level
-- SKIPPED), powering `pipeline.reports.service_delivered`'s executed/planned
-- ratio without a live per-request ClickHouse scan over `updates`.
--
-- `non_executed_trips` counts distinct (service day, trip_id) pairs whose
-- LATEST observation -- argMax by (captured_at, file_name), the same dedup
-- rule as `pipeline.db.build_dedup_ch_sql` -- was either trip-level CANCELED
-- (`schedule_relationship_trip` = 3) or had any stop marked SKIPPED
-- (`schedule_relationship_stop` = 1). A reader sums this column over an
-- arbitrary [from, to] range and subtracts it from the static schedule's
-- planned-trip count for that same range.
--
-- Populated only for agencies whose ingest strategy actually sends
-- `schedule_relationship_*` (today: `static_join`) -- an agency whose feed
-- never sends it gets zero rows here, which the read path distinguishes from
-- "confirmed zero cancellations" via `agencies.ingest_strategy`, not via any
-- row in this table. A day with genuinely zero non-executed trips also has
-- no row (the source aggregation only emits rows that exist), so a reader
-- must SUM with a zero default, never assume row-per-day density.
--
-- `date` is the JST civil day (`toDate(captured_at, 'Asia/Tokyo')`), the same
-- convention as `agg_route_daily_dist.date`.
CREATE TABLE IF NOT EXISTS agg_service_delivered_daily (
    agency_id          INTEGER NOT NULL REFERENCES agencies(agency_id),
    date               DATE    NOT NULL,
    non_executed_trips INTEGER NOT NULL,
    -- The (agency_id, date) PK prefix serves the sargable range scan
    -- (WHERE agency_id=? AND date BETWEEN ? AND ?), so no secondary index.
    PRIMARY KEY (agency_id, date)
);
