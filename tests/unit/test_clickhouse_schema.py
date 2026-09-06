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
