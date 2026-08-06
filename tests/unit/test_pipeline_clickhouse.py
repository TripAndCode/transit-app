import os
from datetime import datetime, timezone

import clickhouse_connect
import pytest

from db.clickhouse.bootstrap import apply_schema
from pipeline.clickhouse import distinct_file_names, insert_updates, max_captured_at


def _ch_test_client():
    return clickhouse_connect.get_client(
        host="localhost",
        port=int(os.environ.get("CLICKHOUSE_TEST_PORT", "8124")),
        username="transit",
        password="transit",
        database="transit_test",
    )


@pytest.fixture
def ch_client():
    client = _ch_test_client()
    client.command("DROP TABLE IF EXISTS updates")
    apply_schema(client)
    yield client
    client.close()


pytestmark = pytest.mark.skipif(os.environ.get("RUN_CH_INTEGRATION") != "1", reason="requires `make ch-test`")


def test_insert_updates_prepends_agency_id(ch_client):
    rows = [("a/000001.pb", "2026-01-01T10:00:00Z", "T1", "weekday", "10:00", "R1", 1, 30)]
    n = insert_updates(ch_client, agency_id=7, rows=rows)
    assert n == 1
    result = ch_client.query("SELECT agency_id, trip_id FROM updates")
    assert result.result_rows == [(7, "T1")]


def test_distinct_file_names_scoped_to_agency(ch_client):
    insert_updates(ch_client, agency_id=1, rows=[("a/1.pb", "2026-01-01T10:00:00Z", "T1", None, None, "R1", 1, 30)])
    insert_updates(ch_client, agency_id=2, rows=[("b/1.pb", "2026-01-01T10:00:00Z", "T1", None, None, "R1", 1, 30)])
    assert distinct_file_names(ch_client, agency_id=1) == {"a/1.pb"}
    assert distinct_file_names(ch_client, agency_id=2) == {"b/1.pb"}


def test_max_captured_at_none_when_empty(ch_client):
    assert max_captured_at(ch_client, agency_id=99) is None


def test_max_captured_at_returns_latest(ch_client):
    insert_updates(
        ch_client,
        agency_id=1,
        rows=[
            ("a/1.pb", "2026-01-01T10:00:00Z", "T1", None, None, "R1", 1, 30),
            ("a/2.pb", "2026-01-02T10:00:00Z", "T1", None, None, "R1", 1, 30),
        ],
    )
    result = max_captured_at(ch_client, agency_id=1)
    assert result == datetime(2026, 1, 2, 10, 0, 0, tzinfo=timezone.utc)
