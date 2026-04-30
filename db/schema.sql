CREATE EXTENSION IF NOT EXISTS postgis;
CREATE EXTENSION IF NOT EXISTS vector;

-- Master catalog
CREATE TABLE IF NOT EXISTS agencies (
    agency_id   SERIAL PRIMARY KEY,
    agency_name TEXT NOT NULL,
    feed_url    TEXT NOT NULL,
    static_url  TEXT,
    timezone    TEXT NOT NULL DEFAULT 'Asia/Tokyo',
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Raw GTFS-RT data
CREATE TABLE IF NOT EXISTS updates (
    id             BIGSERIAL PRIMARY KEY,
    agency_id      INTEGER NOT NULL REFERENCES agencies(agency_id),
    file_name      TEXT NOT NULL,
    captured_at    TIMESTAMPTZ NOT NULL,
    trip_id        TEXT NOT NULL,
    service_type   TEXT NOT NULL,
    scheduled_time TEXT NOT NULL,
    route_code     TEXT NOT NULL,
    stop_sequence  INTEGER,
    dep_delay      INTEGER,
    UNIQUE (agency_id, file_name, route_code, stop_sequence)
);
CREATE INDEX IF NOT EXISTS idx_updates_agency_route ON updates (agency_id, route_code);
CREATE INDEX IF NOT EXISTS idx_updates_captured_at  ON updates (captured_at);

-- Static GTFS: stops (with PostGIS geometry)
CREATE TABLE IF NOT EXISTS static_stops (
    agency_id  INTEGER NOT NULL REFERENCES agencies(agency_id),
    stop_id    TEXT    NOT NULL,
    stop_name  TEXT,
    stop_lat   DOUBLE PRECISION,
    stop_lon   DOUBLE PRECISION,
    geom       GEOMETRY(Point, 4326),
    PRIMARY KEY (agency_id, stop_id)
);

CREATE TABLE IF NOT EXISTS static_stop_times (
    agency_id      INTEGER NOT NULL REFERENCES agencies(agency_id),
    trip_id        TEXT    NOT NULL,
    stop_sequence  INTEGER NOT NULL,
    stop_id        TEXT    NOT NULL,
    arrival_time   TEXT,
    departure_time TEXT,
    PRIMARY KEY (agency_id, trip_id, stop_sequence)
);
CREATE INDEX IF NOT EXISTS idx_sst_trip ON static_stop_times(agency_id, trip_id);
CREATE INDEX IF NOT EXISTS idx_sst_stop ON static_stop_times(agency_id, stop_id);

CREATE TABLE IF NOT EXISTS static_trips (
    agency_id     INTEGER NOT NULL REFERENCES agencies(agency_id),
    trip_id       TEXT    NOT NULL,
    route_id      TEXT,
    trip_headsign TEXT,
    shape_id      TEXT,
    PRIMARY KEY (agency_id, trip_id)
);
CREATE INDEX IF NOT EXISTS idx_str_route ON static_trips(agency_id, route_id);

CREATE TABLE IF NOT EXISTS static_routes (
    agency_id        INTEGER NOT NULL REFERENCES agencies(agency_id),
    route_id         TEXT    NOT NULL,
    route_short_name TEXT,
    PRIMARY KEY (agency_id, route_id)
);

CREATE TABLE IF NOT EXISTS static_calendar_dates (
    agency_id      INTEGER NOT NULL REFERENCES agencies(agency_id),
    service_id     TEXT    NOT NULL,
    date           TEXT    NOT NULL,
    exception_type INTEGER,
    PRIMARY KEY (agency_id, service_id, date)
);

-- Aggregation tables
CREATE TABLE IF NOT EXISTS agg_route_stats (
    agency_id      INTEGER NOT NULL REFERENCES agencies(agency_id),
    route_code     TEXT    NOT NULL,
    service_type   TEXT    NOT NULL,
    avg_min        REAL,
    p50_min        REAL,
    p90_min        REAL,
    late_5min_plus INTEGER,
    on_time_pct    REAL,
    late5_pct      REAL,
    samples        INTEGER,
    PRIMARY KEY (agency_id, route_code, service_type)
);

CREATE TABLE IF NOT EXISTS agg_route_hour (
    agency_id      INTEGER NOT NULL REFERENCES agencies(agency_id),
    route_code     TEXT    NOT NULL,
    service_type   TEXT    NOT NULL,
    scheduled_time TEXT    NOT NULL,
    avg_min        REAL,
    p50_min        REAL,
    p90_min        REAL,
    samples        INTEGER,
    PRIMARY KEY (agency_id, route_code, service_type, scheduled_time)
);

CREATE TABLE IF NOT EXISTS agg_route_dow (
    agency_id    INTEGER NOT NULL REFERENCES agencies(agency_id),
    route_code   TEXT    NOT NULL,
    service_type TEXT    NOT NULL,
    dow          TEXT    NOT NULL,
    avg_min      REAL,
    samples      INTEGER,
    PRIMARY KEY (agency_id, route_code, service_type, dow)
);

CREATE TABLE IF NOT EXISTS agg_daily_trend (
    agency_id    INTEGER NOT NULL REFERENCES agencies(agency_id),
    date         TEXT    NOT NULL,
    route_code   TEXT    NOT NULL,
    service_type TEXT    NOT NULL,
    avg_min      REAL,
    samples      INTEGER,
    PRIMARY KEY (agency_id, date, route_code, service_type)
);

CREATE TABLE IF NOT EXISTS agg_stop_seq (
    agency_id     INTEGER NOT NULL REFERENCES agencies(agency_id),
    route_code    TEXT    NOT NULL,
    stop_sequence INTEGER NOT NULL,
    stop_name     TEXT,
    avg_min       REAL,
    samples       INTEGER,
    PRIMARY KEY (agency_id, route_code, stop_sequence)
);

-- RAG chunks (pgvector)
CREATE TABLE IF NOT EXISTS rag_chunks (
    chunk_id     TEXT    NOT NULL,
    agency_id    INTEGER NOT NULL REFERENCES agencies(agency_id),
    content      TEXT    NOT NULL,
    embedding    VECTOR(384),
    content_hash TEXT    NOT NULL,
    embedded_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (agency_id, chunk_id)
);
