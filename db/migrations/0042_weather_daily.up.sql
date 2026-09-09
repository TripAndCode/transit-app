-- Observed daily weather per agency, keyed to the same JST service date the
-- agg_* tables use, powering the wet-day/dry-day service comparison in
-- pipeline.reports.weather (item 105).
--
-- This is HISTORICAL OBSERVATION ONLY, never a forecast: every row is a
-- value JMA already published for a day that has passed. Nothing in this
-- pipeline predicts weather, and no row here may be written from a forecast
-- product.
--
-- Provenance and attribution
-- --------------------------
-- Rows come from JMA's published observations for ONE representative
-- station (or grid cell) chosen per agency, recorded in station_id /
-- station_name on every row rather than assumed by the reader: an agency's
-- service area is wider than a single observation point, so which point was
-- used is part of the datum, not configuration to look up elsewhere. JMA
-- permits secondary and commercial use under its public-data terms provided
-- the source is indicated and any processing is disclosed, so `source`
-- retains the machine-readable provenance of each row and
-- pipeline.weather.weather_attribution renders the human-readable
-- source-plus-processing credit that every surface showing these figures
-- must display.
--
-- Revisions and availability lag
-- ------------------------------
-- JMA publishes a preliminary value (速報値) first and may revise it before
-- it becomes final (確定値), and recent days are simply not published yet.
-- `quality` records which state a row holds so a re-import can apply a
-- revision (see pipeline.weather.upsert_weather_observations, which lets a
-- final value replace a preliminary one but never the reverse). A day with
-- no row at all is "not yet available", which the report surfaces as
-- reduced coverage instead of silently treating the day as dry.
--
-- Nullable measurements: an individual element can be missing (sensor
-- outage, element not observed at that station) while others are present,
-- so each measurement is independently nullable. A row where every
-- measurement is NULL carries nothing and is not stored at all.
CREATE TABLE IF NOT EXISTS weather_daily (
    agency_id     INTEGER NOT NULL REFERENCES agencies(agency_id),
    -- JST civil day, matching agg_route_daily_dist.date (analyze pins the
    -- session timezone to Asia/Tokyo), so the join needs no conversion.
    date          DATE    NOT NULL,
    station_id    TEXT    NOT NULL,
    station_name  TEXT    NOT NULL,
    source        TEXT    NOT NULL DEFAULT 'jma',
    precip_mm     DOUBLE PRECISION,
    temp_max_c    DOUBLE PRECISION,
    temp_min_c    DOUBLE PRECISION,
    quality       TEXT    NOT NULL CHECK (quality IN ('preliminary', 'final')),
    retrieved_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    -- One representative observation per agency-day: the report joins
    -- agg_route_daily_dist 1:1 on (agency_id, date), so a second row for
    -- the same day (e.g. a neighbouring station) would double-count that
    -- day's service. Changing an agency's representative station overwrites
    -- its rows rather than adding parallel ones.
    PRIMARY KEY (agency_id, date),
    CONSTRAINT weather_daily_precip_nonneg CHECK (precip_mm IS NULL OR precip_mm >= 0),
    CONSTRAINT weather_daily_has_measurement CHECK (
        precip_mm IS NOT NULL OR temp_max_c IS NOT NULL OR temp_min_c IS NOT NULL
    )
);
