-- Each route's running time per timetable segment, service date and time
-- band: the base for the timetable-fit figures (docs/methodology.md). Built by
-- analyze (pipeline.agg_sql.segment_runtime_sql) from the stop visits it
-- pairs with the archived timetable in force on each date, for the agencies
-- whose feed sends arrivals; a reader checks the agency's field-coverage
-- verdict before trusting a row.
--
-- A segment is the pair of stops the timetable calls at in turn, from_stop_id
-- then to_stop_id, under the route; trips of different variants that share
-- the pair share its row. time_band is the band of the scheduled departure
-- from from_stop_id on the wall clock (api.range.TIME_BAND_RANGES), 'none'
-- when the timetable gives none. Every service id of a date pools into one
-- row, since the service filter selects dates.
--
-- run_sec holds every observed running time in seconds, so a reader pools any
-- set of days and takes exact percentiles; it is sorted, so rebuilding the same
-- visits writes the same row. scheduled_sum_sec and
-- scheduled_samples are the scheduled running times of the same trip-days.
-- holds of hold_eligible count the on-time-or-early arrivals at from_stop_id
-- that dwelt beyond the scheduled dwell, and terminal_early of
-- terminal_arrivals the arrivals at a trip's last stop that came early.
-- to_stop_sequence is the lowest sequence the to stop had that day, an order
-- for display.
CREATE TABLE IF NOT EXISTS agg_route_segment_runtime (
    agency_id         INTEGER   NOT NULL REFERENCES agencies(agency_id),
    date              DATE      NOT NULL,
    route_code        TEXT      NOT NULL,
    from_stop_id      TEXT      NOT NULL,
    to_stop_id        TEXT      NOT NULL,
    time_band         TEXT      NOT NULL,
    to_stop_sequence  INTEGER   NOT NULL,
    run_sec           INTEGER[] NOT NULL,
    scheduled_samples INTEGER   NOT NULL,
    scheduled_sum_sec BIGINT    NOT NULL,
    hold_eligible     INTEGER   NOT NULL,
    holds             INTEGER   NOT NULL,
    terminal_arrivals INTEGER   NOT NULL,
    terminal_early    INTEGER   NOT NULL,
    -- (agency_id, date) leads, so analyze's purge of rebuilt dates is a range scan.
    PRIMARY KEY (agency_id, date, route_code, from_stop_id, to_stop_id, time_band)
);

-- A read names one route over a range of dates.
CREATE INDEX IF NOT EXISTS idx_agg_route_segment_runtime_route
    ON agg_route_segment_runtime (agency_id, route_code, date);
