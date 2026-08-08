-- NOTE: the live dev ClickHouse instance (~575M-row real-data `updates` table)
-- predates the trip_id/scheduled_time LowCardinality change below and still
-- has the old String/Nullable(String) types — see db/clickhouse/bootstrap.py
-- for the one-time ALTER TABLE needed to migrate it (a separate, deliberate
-- action, not something apply_schema does automatically).
CREATE TABLE IF NOT EXISTS updates (
    agency_id      UInt16,
    captured_at    DateTime64(0, 'UTC'),
    file_name      LowCardinality(String),
    trip_id        LowCardinality(String),
    service_type   LowCardinality(Nullable(String)),
    scheduled_time LowCardinality(Nullable(String)),
    route_code     LowCardinality(String),
    stop_sequence  UInt16,
    dep_delay      Nullable(Int32)
) ENGINE = MergeTree
PARTITION BY toYYYYMM(captured_at)
ORDER BY (agency_id, captured_at, route_code, trip_id, stop_sequence);
