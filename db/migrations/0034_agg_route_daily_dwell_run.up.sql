-- Per-route, per-day dwell-time / running-time DISTRIBUTION -- the
-- decomposition view alongside agg_route_daily_dist's delay histogram (item
-- 95). Dwell time is time spent AT a stop (this visit's departure minus its
-- own arrival); running time is time spent BETWEEN stops (this visit's
-- arrival minus the previous stop visit's departure). Both need an actual
-- arrival timestamp, which only exists where the RT feed sent
-- StopTimeUpdate.arrival (`arr_delay` non-NULL) -- see pipeline/dwell_run.py
-- and pipeline/analyze.py's builder for this table.
--
-- Only populated for agencies whose ingest_strategy is confirmed to send
-- `arr_delay` (today: `static_join`) AND have a static schedule loaded (the
-- schedule's arrival_time/departure_time are what `arr_delay`/`dep_delay`
-- are added to) -- an agency failing either condition gets zero rows here,
-- which the read path distinguishes from "confirmed zero dwell/running
-- time" via `agencies.ingest_strategy`, never via row presence alone (same
-- convention as `agg_service_delivered_daily`).
--
-- Exact stats compose by summing: dwell_avg = SUM(dwell_sum_sec)/
-- SUM(dwell_samples), and likewise for running time. Percentiles don't
-- compose, so `hist_dwell`/`hist_run` hold fixed-width histograms (see
-- pipeline/dwell_run.py's own bucket bounds, distinct from
-- pipeline/histogram.py's delay-scaled ones); p50/p90 are interpolated from
-- the merged buckets over the range.
--
-- `date` is captured_at::date in the session timezone (Asia/Tokyo, same as
-- agg_route_daily_dist.date).
CREATE TABLE IF NOT EXISTS agg_route_daily_dwell_run (
    agency_id     INTEGER NOT NULL REFERENCES agencies(agency_id),
    date          DATE    NOT NULL,
    route_code    TEXT    NOT NULL,
    service_type  TEXT    NOT NULL,
    dwell_samples INTEGER NOT NULL,
    dwell_sum_sec BIGINT  NOT NULL,
    hist_dwell    INTEGER[] NOT NULL,  -- bucket counts; length = pipeline.dwell_run.DWELL_N_BUCKETS
    run_samples   INTEGER NOT NULL,
    run_sum_sec   BIGINT  NOT NULL,
    hist_run      INTEGER[] NOT NULL,  -- bucket counts; length = pipeline.dwell_run.RUN_N_BUCKETS
    -- The (agency_id, date) PK prefix serves the sargable range scan
    -- (WHERE agency_id=? AND date BETWEEN ? AND ?), so no secondary index.
    PRIMARY KEY (agency_id, date, route_code, service_type),
    -- Guard the fixed histogram widths so any future bucket-count drift
    -- fails loudly at write time instead of silently mis-merging in the
    -- read path's element-wise array sum.
    CONSTRAINT agg_route_daily_dwell_run_hist_dwell_len CHECK (array_length(hist_dwell, 1) = 26),
    CONSTRAINT agg_route_daily_dwell_run_hist_run_len CHECK (array_length(hist_run, 1) = 32)
);
