-- Each agency's feed-quality scorecard per calendar month of service date
-- (pipeline.feed_quality, built by analyze). A figure the feed or the app
-- cannot supply is NULL beside its reason, never 0, and the CHECKs below hold
-- every figure and its reason to exactly one of the two.
--
-- through_date is the month's last service date counted: its last day, or for
-- the month in progress the last closed service day. Every figure counts the
-- service dates from the month's first day through it.
CREATE TABLE IF NOT EXISTS feed_quality_monthly (
    agency_id                        INT          NOT NULL REFERENCES agencies(agency_id) ON DELETE CASCADE,
    month                            DATE         NOT NULL CHECK (EXTRACT(DAY FROM month) = 1),
    through_date                     DATE         NOT NULL,
    covered_days                     INT          NOT NULL,
    window_days                      INT          NOT NULL,
    observed_trips                   BIGINT,
    planned_trips                    BIGINT,
    rt_completeness_pct              NUMERIC(7, 1),
    rt_completeness_missing          TEXT,
    observed_stop_events             BIGINT,
    planned_stop_events              BIGINT,
    stop_plan_days                   INT          NOT NULL,
    stop_completeness_pct            NUMERIC(7, 1),
    stop_completeness_missing        TEXT,
    message_age_polls                INT          NOT NULL,
    message_age_p50_sec              DOUBLE PRECISION,
    message_age_p95_sec              DOUBLE PRECISION,
    message_age_missing              TEXT,
    raw_samples                      BIGINT       NOT NULL,
    clamp_count                      BIGINT       NOT NULL,
    clamp_pct                        NUMERIC(7, 2),
    clamp_missing                    TEXT,
    stop_time_updates                BIGINT       NOT NULL,
    stop_id_pct                      NUMERIC(4, 1),
    arr_delay_pct                    NUMERIC(4, 1),
    schedule_relationship_trip_pct   NUMERIC(4, 1),
    schedule_relationship_stop_pct   NUMERIC(4, 1),
    field_coverage_missing           TEXT,
    vehicle_positions_available      BOOLEAN,
    vehicle_positions_missing        TEXT,
    PRIMARY KEY (agency_id, month),
    CHECK (through_date >= month AND through_date < month + INTERVAL '1 month'),
    CHECK ((rt_completeness_pct IS NULL) = (rt_completeness_missing IS NOT NULL)),
    CHECK ((stop_completeness_pct IS NULL) = (stop_completeness_missing IS NOT NULL)),
    CHECK ((message_age_p50_sec IS NULL) = (message_age_missing IS NOT NULL)),
    CHECK ((message_age_p95_sec IS NULL) = (message_age_missing IS NOT NULL)),
    CHECK ((clamp_pct IS NULL) = (clamp_missing IS NOT NULL)),
    CHECK ((stop_id_pct IS NULL) = (field_coverage_missing IS NOT NULL)),
    CHECK ((arr_delay_pct IS NULL) = (field_coverage_missing IS NOT NULL)),
    CHECK ((schedule_relationship_trip_pct IS NULL) = (field_coverage_missing IS NOT NULL)),
    CHECK ((schedule_relationship_stop_pct IS NULL) = (field_coverage_missing IS NOT NULL)),
    CHECK ((vehicle_positions_available IS NULL) = (vehicle_positions_missing IS NOT NULL))
);

-- The stop events (stop_times rows) of each saved day's planned trips, saved
-- beside them by the same capture (pipeline.planned_trips), for the
-- scorecard's stop-event completeness. NULL on a day saved before the column
-- existed: its timetable may be gone, so its count is unknown and the
-- scorecard leaves that day out of the ratio rather than guessing it.
ALTER TABLE planned_trips_daily ADD COLUMN IF NOT EXISTS planned_stop_events BIGINT;
