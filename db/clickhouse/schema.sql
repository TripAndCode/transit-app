CREATE TABLE IF NOT EXISTS updates (
    agency_id      UInt16,
    captured_at    DateTime64(0, 'UTC'),
    file_name      LowCardinality(String),
    trip_id        String,
    service_type   LowCardinality(Nullable(String)),
    scheduled_time Nullable(String),
    route_code     LowCardinality(String),
    stop_sequence  UInt16,
    dep_delay      Nullable(Int32)
) ENGINE = MergeTree
PARTITION BY toYYYYMM(captured_at)
ORDER BY (agency_id, captured_at, route_code, trip_id, stop_sequence);
