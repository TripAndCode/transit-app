-- Per-route, per-day delay DISTRIBUTION — powers the range-scoped reports
-- (ranking / on_time / worst_5min) without scanning raw `updates`. Those
-- reports filter by an arbitrary [from, to] date range, so an all-time
-- aggregate (agg_route_stats) can't serve them; this per-day grain sums across
-- the range at read time.
--
-- Exact stats compose by summing: avg = SUM(sum_delay_sec)/SUM(samples),
-- on-time% = SUM(on_time_count)/SUM(samples), worst-5min = SUM(late5_count).
-- Percentiles don't compose, so `hist` holds a fixed-width delay histogram
-- (see pipeline/histogram.py); p50/p90 are interpolated from the merged buckets
-- over the range (approximation bounded by one bucket width — fine for ranking).
--
-- `date` is captured_at::date in the session timezone, which analyze pins to
-- Asia/Tokyo (gtfs_pipeline._get_conn) — the same JST civil day the API reads
-- under, so the agg fast path and the live fallback agree.
CREATE TABLE IF NOT EXISTS agg_route_daily_dist (
    agency_id     INTEGER NOT NULL REFERENCES agencies(agency_id),
    date          DATE    NOT NULL,
    route_code    TEXT    NOT NULL,
    service_type  TEXT    NOT NULL,
    samples       INTEGER NOT NULL,
    sum_delay_sec BIGINT  NOT NULL,
    on_time_count INTEGER NOT NULL,  -- dep_delay <= 60s
    late5_count   INTEGER NOT NULL,  -- dep_delay > 300s
    hist          INTEGER[] NOT NULL,  -- bucket counts; length = histogram.N_BUCKETS
    -- The (agency_id, date) PK prefix serves the sargable range scan
    -- (WHERE agency_id=? AND date BETWEEN ? AND ?), so no secondary index.
    PRIMARY KEY (agency_id, date, route_code, service_type),
    -- Guard the fixed histogram width (histogram.N_BUCKETS) so any future
    -- bucket-count drift fails loudly at write time instead of silently
    -- mis-merging in the read path's element-wise array sum.
    CONSTRAINT agg_route_daily_dist_hist_len CHECK (array_length(hist, 1) = 37)
);
