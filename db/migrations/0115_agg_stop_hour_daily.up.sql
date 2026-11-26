-- Each stop's observed departures per service date and service-day hour: what
-- the supply map's stop panel sets beside the departures the timetable plans
-- there in each hour (pipeline.reports.stop_supply). Computed in ClickHouse by
-- analyze (pipeline.ch_aggregates) and copied here.
--
-- hour is the hour of the departure's scheduled time on the service day's
-- clock, 4 to 27: a departure written at face value before the 04:00 cutover
-- (00:40) is the same night's hour 24, as the planned side counts it
-- (pipeline.timetable_sampling). A departure with no scheduled time is in no
-- hour and has no row. sum_delay_sec is the exact SUM(dep_delay); on_time_count
-- and late5_count count departures at most one minute and more than five
-- minutes late, early_count those more than the default early tolerance early,
-- so a pooled read takes the default on-time window exactly
-- (pipeline.histogram.on_time_from_columns).
CREATE TABLE IF NOT EXISTS agg_stop_hour_daily (
    agency_id     INTEGER  NOT NULL REFERENCES agencies(agency_id),
    stop_id       TEXT     NOT NULL,
    date          DATE     NOT NULL,
    hour          SMALLINT NOT NULL,
    samples       INTEGER  NOT NULL,
    sum_delay_sec BIGINT   NOT NULL,
    on_time_count INTEGER  NOT NULL,
    late5_count   INTEGER  NOT NULL,
    early_count   INTEGER  NOT NULL,
    -- A read names one stop over a range of dates.
    PRIMARY KEY (agency_id, stop_id, date, hour)
);

-- analyze's purge of rebuilt dates names the agency and a set of dates.
CREATE INDEX IF NOT EXISTS idx_agg_stop_hour_daily_agency_date ON agg_stop_hour_daily (agency_id, date);
