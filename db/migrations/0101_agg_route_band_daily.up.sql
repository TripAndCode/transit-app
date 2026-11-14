-- Each route's departures per service date and time band: what a time-band
-- Pulse (pipeline.reports.overview) reads instead of scanning `updates`.
-- Computed in ClickHouse by analyze (pipeline.ch_aggregates) and copied here.
--
-- time_band is the band of the departure's scheduled time on the wall clock
-- (api.range.time_band_ch); a departure with no scheduled time is in no band
-- and has no row. route_code is '' for a departure no route resolves: the
-- headline and the daily figures count it, and no route list names it. Every
-- service id of a date pools into one row, since the service filter selects
-- dates. sum_delay_sec and sum_late_sec are the exact SUM(dep_delay) and
-- SUM(GREATEST(dep_delay, 0)) in seconds, so pooled rows divide once, at the
-- end.
CREATE TABLE IF NOT EXISTS agg_route_band_daily (
    agency_id     INTEGER NOT NULL REFERENCES agencies(agency_id),
    date          DATE    NOT NULL,
    route_code    TEXT    NOT NULL,
    time_band     TEXT    NOT NULL,
    samples       INTEGER NOT NULL,
    sum_delay_sec BIGINT  NOT NULL,
    sum_late_sec  BIGINT  NOT NULL,
    -- (agency_id, date) leads, so analyze's purge of rebuilt dates is a range scan.
    PRIMARY KEY (agency_id, date, route_code, time_band)
);

-- A read names one band over a range of dates and takes every other column,
-- so the index covers them and answers the read without visiting the table.
CREATE INDEX IF NOT EXISTS idx_agg_route_band_daily_band ON agg_route_band_daily (agency_id, time_band, date)
    INCLUDE (route_code, samples, sum_delay_sec, sum_late_sec);
