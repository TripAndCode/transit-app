-- NOTE: the live dev ClickHouse instance (a large, real-data `updates` table)
-- predates the trip_id/scheduled_time LowCardinality change below and still
-- has the old String/Nullable(String) types — see db/clickhouse/bootstrap.py
-- for the one-time ALTER TABLE needed to migrate it (a separate, deliberate
-- action, not something apply_schema does automatically). Every nullable
-- column added below since (scheduled_sec onward) does NOT need that manual
-- step: apply_schema's _NEW_NULLABLE_COLUMNS self-heals a pre-existing table
-- via idempotent ADD COLUMN IF NOT EXISTS (see bootstrap.py).
CREATE TABLE IF NOT EXISTS updates (
    agency_id      UInt16,
    captured_at    DateTime64(0, 'UTC'),
    file_name      LowCardinality(String),
    trip_id        LowCardinality(String),
    service_type   LowCardinality(Nullable(String)),
    scheduled_time LowCardinality(Nullable(String)),
    route_code     LowCardinality(Nullable(String)),
    stop_sequence  UInt16,
    dep_delay      Nullable(Int32),
    -- Raw seconds-since-service-day-start, e.g. 91800 for a GTFS
    -- "25:30:00" post-midnight-continuation departure_time. Unlike
    -- scheduled_time (a same-day "HH:MM[:SS]" string that has no way to
    -- represent an hour >= 24), scheduled_sec holds a value for those rows
    -- too instead of the ingest strategy having to drop them. NULL when
    -- departure_time itself didn't parse as a time at all (empty/malformed).
    scheduled_sec  Nullable(Int32),
    -- RT-sourced fields confirmed present in at least one currently-configured
    -- agency's feed (see pipeline/strategies/*.py's parse_feed docstrings for
    -- which agencies populate which of these) -- absent for any agency whose
    -- feed doesn't send the underlying protobuf field, and unread by any
    -- agg_*/dashboard consumer today.
    stop_id                    LowCardinality(Nullable(String)),
    arr_delay                  Nullable(Int32),
    schedule_relationship_trip Nullable(UInt8),
    schedule_relationship_stop Nullable(UInt8),
    feed_timestamp             Nullable(UInt64),
    -- static_trips.static_version_id (the loaded GTFS static zip's filename
    -- stem, e.g. "gtfs_static_20260101") for the static feed version this RT
    -- row's static_join matched against. NULL when the JOIN missed (no
    -- matching static_trips row) or for ingest strategies that don't join
    -- static data at all (e.g. aomori_regex.py).
    static_version_id LowCardinality(Nullable(String))
) ENGINE = MergeTree
PARTITION BY toYYYYMM(captured_at)
ORDER BY (agency_id, captured_at, route_code, trip_id, stop_sequence)
-- route_code sits in the sort key AND is Nullable (Postgres's updates.route_code
-- was nullable — migration 0006 — because static_join/aomori_regex can both
-- produce a row with no resolvable route). ClickHouse rejects a nullable
-- sort-key column unless this setting is on, and the null-map overhead here
-- is small next to a NOT NULL column that silently drops rows on insert.
SETTINGS allow_nullable_key = 1;

-- Realtime observations only: the collector push and ingest_live write here,
-- and nothing else does. `updates` receives each closed JST day from this
-- table by daily promotion (pipeline/promote.py), so it and every aggregate
-- hold closed days only. Same columns as `updates`, because promotion copies
-- by column name. Daily partitions, and a TTL that holds today, yesterday and
-- one day of slack for a late promotion. A day still here when its TTL
-- passes is gone.
CREATE TABLE IF NOT EXISTS updates_live AS updates
ENGINE = MergeTree
PARTITION BY toYYYYMMDD(captured_at)
ORDER BY (agency_id, captured_at, route_code, trip_id, stop_sequence)
TTL toDateTime(captured_at) + INTERVAL 3 DAY DELETE
SETTINGS allow_nullable_key = 1;

-- Aggregates computed in ClickHouse by pipeline/ch_aggregates.py. Each has the
-- columns and types of the Postgres table of the same name, which holds the copy
-- the API reads. One partition per agency and month of service date, the unit a
-- run replaces.
CREATE TABLE IF NOT EXISTS agg_hour_daily (
    agency_id     Int32,
    date          Date,
    hour          Int16,
    avg_min       Decimal(18, 2),
    samples       Int32,
    sum_delay_sec Int64
) ENGINE = MergeTree
PARTITION BY (agency_id, toYYYYMM(date))
ORDER BY (agency_id, date, hour);

CREATE TABLE IF NOT EXISTS agg_daily_trend (
    agency_id     Int32,
    date          Date,
    route_code    String,
    service_type  String,
    avg_min       Float32,
    samples       Int32,
    sum_delay_sec Int64,
    sum_late_sec  Int64
) ENGINE = MergeTree
PARTITION BY (agency_id, toYYYYMM(date))
ORDER BY (agency_id, date, route_code, service_type);

CREATE TABLE IF NOT EXISTS agg_route_daily (
    agency_id       Int32,
    date            Date,
    route_code      String,
    service_type    String,
    avg_delay_sec   Int32,
    worst_delay_sec Int32,
    trips_observed  Int32,
    samples         Int32,
    last_seen_at    DateTime64(0, 'UTC'),
    sum_delay_sec   Int64
) ENGINE = MergeTree
PARTITION BY (agency_id, toYYYYMM(date))
ORDER BY (agency_id, date, route_code, service_type);

CREATE TABLE IF NOT EXISTS agg_route_daily_dist (
    agency_id     Int32,
    date          Date,
    route_code    String,
    service_type  String,
    samples       Int32,
    sum_delay_sec Int64,
    on_time_count Int32,
    late5_count   Int32,
    hist          Array(Int32)
) ENGINE = MergeTree
PARTITION BY (agency_id, toYYYYMM(date))
ORDER BY (agency_id, date, route_code, service_type);

-- The exact delay distribution behind the whole-history aggregates: per agency,
-- service date, route, typed service and scheduled time, one row per dep_delay
-- value with the number of stop events that took it. ClickHouse only, never
-- copied. Month partitions like the date-keyed tables, so a run recounts only
-- the dates it touches and the whole-history tables are finalized from these
-- counts instead of from all of `updates`.
CREATE TABLE IF NOT EXISTS route_delay_counts (
    agency_id     Int32,
    date          Date,
    route_code    String,
    service_type  String,
    scheduled_sec Nullable(Int32),
    dep_delay     Int32,
    samples       Int32,
    sum_delay_sec Int64
) ENGINE = MergeTree
PARTITION BY (agency_id, toYYYYMM(date))
ORDER BY (agency_id, date, route_code, service_type, scheduled_sec, dep_delay)
SETTINGS allow_nullable_key = 1;

-- Whole-history aggregates, finalized from route_delay_counts. One partition per
-- agency, replaced whole by a run that changed anything.
CREATE TABLE IF NOT EXISTS agg_route_stats (
    agency_id      Int32,
    route_code     String,
    service_type   String,
    avg_min        Float32,
    p50_min        Float32,
    p90_min        Float32,
    late_5min_plus Int32,
    on_time_pct    Float32,
    late5_pct      Float32,
    samples        Int32,
    sum_delay_sec  Int64
) ENGINE = MergeTree
PARTITION BY agency_id
ORDER BY (agency_id, route_code, service_type);

-- scheduled_time is written HH:MM:SS, the text form of the Postgres `time` it is copied into.
CREATE TABLE IF NOT EXISTS agg_route_hour (
    agency_id      Int32,
    route_code     String,
    service_type   String,
    scheduled_time String,
    avg_min        Float32,
    p50_min        Float32,
    p90_min        Float32,
    samples        Int32,
    sum_delay_sec  Int64
) ENGINE = MergeTree
PARTITION BY agency_id
ORDER BY (agency_id, route_code, service_type, scheduled_time);

CREATE TABLE IF NOT EXISTS agg_route_hour_dow (
    agency_id     Int32,
    route_code    String,
    service_type  String,
    dow           Int16,
    hour          Int16,
    avg_min       Float32,
    samples       Int32,
    sum_delay_sec Int64
) ENGINE = MergeTree
PARTITION BY agency_id
ORDER BY (agency_id, route_code, service_type, dow, hour);
