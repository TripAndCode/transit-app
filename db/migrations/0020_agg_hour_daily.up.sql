-- Per-day, per-hour-of-day delay across all routes — powers the Overview
-- peak-hour-by-DOW panels and the reports/trend hourly heatmap without scanning
-- raw `updates`. Those queries need hour-of-day delay restricted to weekday or
-- weekend over a date range; no existing aggregate carries hour AND date, so
-- `_peak_hour_by_dow` (measured ~96% of Overview cold-load time) and
-- compute_hourly_heatmap fell back to the live dedup scan.
--
-- `date` lets the read path filter by DOW (EXTRACT(ISODOW FROM date)) and by
-- range; `hour` is EXTRACT(HOUR FROM scheduled_time). Aggregated across all
-- routes/services (no route/service dimension), so a service/route filter falls
-- back to the live path. `avg_min` is sample-weighted-composable via
-- SUM(avg_min*samples)/SUM(samples) across the merged days.
CREATE TABLE IF NOT EXISTS agg_hour_daily (
    agency_id INTEGER  NOT NULL REFERENCES agencies(agency_id),
    date      DATE     NOT NULL,
    hour      SMALLINT NOT NULL,
    avg_min   NUMERIC(6, 2) NOT NULL,
    samples   INTEGER  NOT NULL,
    -- (agency_id, date) PK prefix serves the sargable range scan.
    PRIMARY KEY (agency_id, date, hour)
);
