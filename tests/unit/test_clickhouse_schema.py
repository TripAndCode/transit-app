import os

import clickhouse_connect
import pytest

from db.clickhouse.bootstrap import apply_schema


def _ch_test_client():
    return clickhouse_connect.get_client(
        host="localhost",
        port=int(os.environ.get("CLICKHOUSE_TEST_PORT", "8124")),
        username="transit",
        password="transit",
        database="transit_test",
    )


@pytest.mark.skipif(os.environ.get("RUN_CH_INTEGRATION") != "1", reason="requires `make ch-test`")
def test_apply_schema_creates_updates_table():
    client = _ch_test_client()
    client.command("DROP TABLE IF EXISTS updates")
    apply_schema(client)
    result = client.query("DESCRIBE TABLE updates")
    columns = [row[0] for row in result.result_rows]
    assert columns == [
        "agency_id",
        "captured_at",
        "file_name",
        "trip_id",
        "service_type",
        "scheduled_time",
        "route_code",
        "stop_sequence",
        "dep_delay",
        "stop_id",
        "arr_delay",
        "schedule_relationship_trip",
        "schedule_relationship_stop",
        "feed_timestamp",
    ]
    # idempotent re-apply must not raise
    apply_schema(client)
    client.close()


@pytest.mark.skipif(os.environ.get("RUN_CH_INTEGRATION") != "1", reason="requires `make ch-test`")
def test_apply_schema_backfills_new_columns_on_pre_existing_table():
    """apply_schema must reach a table that already existed before its five
    newest nullable columns (stop_id, arr_delay, schedule_relationship_trip,
    schedule_relationship_stop, feed_timestamp) were added to schema.sql --
    CREATE TABLE IF NOT EXISTS is a no-op against such a table, so without a
    dedicated ADD COLUMN step, insert_updates' column_names=UPDATE_COLUMNS
    (which now names all five) would have ClickHouse reject its entire
    batch -- not just the new fields -- the moment it ran against a table
    nobody had manually migrated.
    """
    client = _ch_test_client()
    client.command("DROP TABLE IF EXISTS updates")
    # Recreate the OLD (pre-these-columns) 9-column shape directly, bypassing
    # apply_schema/schema.sql entirely, so this test proves the ADD COLUMN
    # step -- not CREATE TABLE IF NOT EXISTS -- is what reaches this table.
    client.command(
        """
        CREATE TABLE updates (
            agency_id      UInt16,
            captured_at    DateTime64(0, 'UTC'),
            file_name      LowCardinality(String),
            trip_id        LowCardinality(String),
            service_type   LowCardinality(Nullable(String)),
            scheduled_time LowCardinality(Nullable(String)),
            route_code     LowCardinality(Nullable(String)),
            stop_sequence  UInt16,
            dep_delay      Nullable(Int32)
        ) ENGINE = MergeTree
        PARTITION BY toYYYYMM(captured_at)
        ORDER BY (agency_id, captured_at, route_code, trip_id, stop_sequence)
        SETTINGS allow_nullable_key = 1
        """
    )
    old_columns = [
        "agency_id",
        "captured_at",
        "file_name",
        "trip_id",
        "service_type",
        "scheduled_time",
        "route_code",
        "stop_sequence",
        "dep_delay",
    ]
    client.insert(
        "updates",
        [(1, "2026-01-01T10:00:00Z", "f1.pb", "T1", "weekday", "10:00", "R1", 1, 30)],
        column_names=old_columns,
    )

    apply_schema(client)

    result = client.query("DESCRIBE TABLE updates")
    columns = [row[0] for row in result.result_rows]
    assert columns == [
        "agency_id",
        "captured_at",
        "file_name",
        "trip_id",
        "service_type",
        "scheduled_time",
        "route_code",
        "stop_sequence",
        "dep_delay",
        "stop_id",
        "arr_delay",
        "schedule_relationship_trip",
        "schedule_relationship_stop",
        "feed_timestamp",
    ]

    # The pre-existing row's old columns survive untouched; the five new
    # columns backfill as NULL for it (ADD COLUMN is metadata-only, it does
    # not rewrite existing parts with a real value).
    rows = client.query(
        "SELECT agency_id, trip_id, dep_delay, stop_id, arr_delay, "
        "schedule_relationship_trip, schedule_relationship_stop, feed_timestamp "
        "FROM updates"
    ).result_rows
    assert rows == [(1, "T1", 30, None, None, None, None, None)]

    # Idempotent: a table that already has the new columns must not raise.
    apply_schema(client)
    client.close()
