import os
from datetime import datetime, timezone

import clickhouse_connect
import pytest

from db.clickhouse.bootstrap import apply_schema
from pipeline.clickhouse import distinct_file_names, insert_updates, max_captured_at, recent_file_name_exists


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


def test_insert_updates_accepts_null_route_code(ch_client):
    """Postgres's updates.route_code was nullable (migration 0006) — both the
    static_join and aomori_regex strategies can produce a row with no
    resolvable route. A non-nullable ClickHouse column would reject the whole
    batch instead of tolerating the row-level gap Postgres did."""
    rows = [("a/000001.pb", "2026-01-01T10:00:00Z", "T1", "weekday", "10:00", None, 1, 30)]
    n = insert_updates(ch_client, agency_id=7, rows=rows)
    assert n == 1
    result = ch_client.query("SELECT route_code FROM updates")
    assert result.result_rows == [(None,)]


def test_insert_updates_dedups_within_batch_first_occurrence_wins(ch_client):
    """Postgres's UNIQUE(agency_id, file_name, trip_id, stop_sequence) + ON
    CONFLICT DO NOTHING silently dropped a repeated (file_name, trip_id,
    stop_sequence) key, first-insert-wins. ClickHouse has no equivalent
    constraint, so insert_updates must reproduce that exact semantic itself —
    otherwise a feed that repeats a TripUpdate entity or stop_sequence within
    one poll (both ingest strategies iterate with no intra-file dedup) would
    double-count in every raw COUNT(*) consumer (agg_feed_health.raw_samples,
    describe_data's total_rows)."""
    rows = [
        ("a/1.pb", "2026-01-01T10:00:00Z", "T1", "weekday", "10:00", "R1", 1, 30),
        ("a/1.pb", "2026-01-01T10:00:00Z", "T1", "weekday", "10:00", "R1", 1, 999),  # same key, different delay
    ]
    n = insert_updates(ch_client, agency_id=1, rows=rows)
    assert n == 1
    result = ch_client.query("SELECT dep_delay FROM updates")
    assert result.result_rows == [(30,)]  # first occurrence wins


def test_recent_file_name_exists_true_when_present(ch_client):
    insert_updates(
        ch_client, agency_id=1, rows=[("live_20260101T100000Z", "2026-01-01T10:00:00Z", "T1", None, None, "R1", 1, 30)]
    )
    since = datetime(2026, 1, 1, 9, 55, 0, tzinfo=timezone.utc)
    assert recent_file_name_exists(ch_client, 1, "live_20260101T100000Z", since) is True
    assert recent_file_name_exists(ch_client, 1, "live_20260101T100100Z", since) is False
    assert recent_file_name_exists(ch_client, 2, "live_20260101T100000Z", since) is False


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
