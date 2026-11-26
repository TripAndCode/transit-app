-- Each stop's observed departures per service date, in time order: what the
-- supply map's stop panel reads each hour's waits from, as run and as
-- timetabled (pipeline.stop_wait). Computed in ClickHouse by analyze
-- (pipeline.ch_aggregates) and copied here.
--
-- scheduled_secs[i] is a departure's scheduled time on the service day's
-- clock, a time written at face value before the 04:00 cutover (00:40) counted
-- past 24:00 as the same night's (88800), so its hour is the one
-- agg_stop_hour_daily files it under; dep_delays[i] is the same departure's
-- delay. The pairs are sorted by time, then delay. A departure with no
-- scheduled time is left out.
CREATE TABLE IF NOT EXISTS agg_stop_departures_daily (
    agency_id      INTEGER   NOT NULL REFERENCES agencies(agency_id),
    stop_id        TEXT      NOT NULL,
    date           DATE      NOT NULL,
    scheduled_secs INTEGER[] NOT NULL,
    dep_delays     INTEGER[] NOT NULL,
    -- A read names one stop over a set of dates.
    PRIMARY KEY (agency_id, stop_id, date),
    CONSTRAINT agg_stop_departures_daily_paired CHECK (cardinality(scheduled_secs) = cardinality(dep_delays))
);

-- analyze's purge of rebuilt dates names the agency and a set of dates.
CREATE INDEX IF NOT EXISTS idx_agg_stop_departures_daily_agency_date ON agg_stop_departures_daily (agency_id, date);
