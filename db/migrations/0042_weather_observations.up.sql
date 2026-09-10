-- Observed daily weather, plus the one representative observation station
-- each agency's service area is keyed to. Powers the rain-vs-dry delay
-- comparison in pipeline.reports.weather.
--
-- OBSERVATION ONLY, NEVER A FORECAST. Every row describes a day the source
-- has already observed and closed out; nothing here predicts future weather
-- or future delays. A row is only written once its whole observed day is
-- available (see pipeline.weather.aggregate_daily), so a day in progress or
-- a day the source hasn't published yet is simply absent rather than
-- present-but-partial -- a partial precipitation total would silently
-- understate rainfall and mis-classify a wet day as dry.
--
-- Attribution travels with the derived figures, not just with the raw rows:
-- the source (JMA / 気象庁) AND the fact that the daily totals/averages are
-- computed here from its sub-hourly observations must both be stated
-- wherever the metric is shown (see pipeline.weather.attribution, which the
-- report response carries to the client).

-- One representative station per agency (PK on agency_id), populated by an
-- operator -- the same convention as ridership_weights (0035) and
-- route_performance_standards (0041). Choosing THE station that represents
-- a service area is a judgement call about geography and station siting, not
-- something derivable from GTFS: an agency spanning a mountain pass may want
-- the inland station even though its depot sits nearer a coastal one. An
-- agency with no row here has no weather metric at all (the report reads
-- "not available"), never a silently guessed nearest station.
--
-- `note` records WHY this station represents this agency, so the metric can
-- honestly describe what it is keyed to. It is PUBLIC, not an internal audit
-- field: the unauthenticated report endpoint returns it verbatim to every
-- caller, because the whole point of the column is to let the rendered figure
-- say which point it speaks for. Write it as operator-facing prose meant to be
-- read by anyone who can see the report -- never internal-only content
-- (contract or contact details, staff names, incident references). Several
-- agencies may share one station_id; that is expected for operators covering
-- the same city.
CREATE TABLE IF NOT EXISTS agency_weather_stations (
    agency_id    INTEGER PRIMARY KEY REFERENCES agencies(agency_id),
    station_id   TEXT NOT NULL,
    station_name TEXT NOT NULL,
    source       TEXT NOT NULL DEFAULT 'jma_amedas',
    note         TEXT,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Keyed by (station, day), NOT by agency: observations are a property of the
-- station, so two agencies pointed at the same station share one row instead
-- of duplicating the fetch, and re-pointing (or deleting) an agency mapping
-- leaves the observed history intact. Deliberately no FK to
-- agency_weather_stations for that reason -- retained history for a station
-- no agency currently references is still valid observed data.
--
-- Every measurement column is nullable: the source publishes precipitation
-- and temperature independently per station, and a station that reports rain
-- but not temperature (or vice versa) must still produce a usable row rather
-- than being dropped. Readers must treat NULL as "not observed here", never
-- as zero -- a NULL precip_mm is not a dry day.
--
-- `retrieved_at` is the revision marker: the source revises recently
-- published observations, so an existing row is re-fetched and overwritten
-- while it is still inside the source's revision window, and this column is
-- what tells the ingest pass how stale its copy is (see
-- pipeline.weather.needs_fetch).
CREATE TABLE IF NOT EXISTS weather_daily_observations (
    station_id   TEXT NOT NULL,
    obs_date     DATE NOT NULL,
    precip_mm    DOUBLE PRECISION CHECK (precip_mm >= 0),
    temp_avg_c   DOUBLE PRECISION,
    temp_max_c   DOUBLE PRECISION,
    temp_min_c   DOUBLE PRECISION,
    source       TEXT NOT NULL,
    retrieved_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (station_id, obs_date)
);
