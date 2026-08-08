-- Recreates the table/index SHAPE only (as of migration 0011, the last one
-- to touch `updates`) — this does NOT restore data. Historical rows now
-- live in ClickHouse only; re-populating this table means re-ingesting
-- from raw_archives/ with the Postgres write path re-enabled, or exporting
-- from ClickHouse. Rolling back this migration alone leaves the table empty.
CREATE TABLE IF NOT EXISTS updates (
    id             BIGSERIAL PRIMARY KEY,
    agency_id      INTEGER NOT NULL REFERENCES agencies(agency_id),
    file_name      TEXT NOT NULL,
    captured_at    TIMESTAMPTZ NOT NULL,
    trip_id        TEXT NOT NULL,
    service_type   TEXT,
    scheduled_time TIME,
    route_code     TEXT,
    stop_sequence  INTEGER NOT NULL DEFAULT 0,
    dep_delay      INTEGER,
    UNIQUE (agency_id, file_name, trip_id, stop_sequence)
);
CREATE INDEX IF NOT EXISTS idx_updates_agency_route ON updates (agency_id, route_code);
CREATE INDEX IF NOT EXISTS idx_updates_agency_at ON updates (agency_id, captured_at);
