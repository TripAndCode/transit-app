"""Proves build_dedup_ch_sql selects the same "latest observation per stop
event" rows as the Postgres DISTINCT ON version it replaces, on a small
fixture designed to exercise the tiebreak (two files, same captured_at
second, different file_name)."""

import os
from datetime import datetime, timezone

import clickhouse_connect
import pytest

from pipeline.db import build_dedup_ch_sql

FIXTURE_ROWS = [
    # (agency_id, captured_at, file_name, trip_id, service_type,
    #  scheduled_time, route_code, stop_sequence, dep_delay)
    (1, datetime(2026, 1, 1, 10, 0, 0, tzinfo=timezone.utc), "a/000001.pb", "T1", "weekday", "10:00", "R1", 1, 30),
    # Same group (route/service/sched/trip/date/stop), same captured_at second, later file_name — must win the tiebreak.
    (1, datetime(2026, 1, 1, 10, 0, 0, tzinfo=timezone.utc), "a/000002.pb", "T1", "weekday", "10:00", "R1", 1, 90),
    # A later captured_at for the same group — must win over both rows above.
    (1, datetime(2026, 1, 1, 10, 5, 0, tzinfo=timezone.utc), "a/000003.pb", "T1", "weekday", "10:00", "R1", 1, 120),
    # Different agency — must never appear when filtering agency_id=1.
    (2, datetime(2026, 1, 1, 10, 5, 0, tzinfo=timezone.utc), "b/000001.pb", "T1", "weekday", "10:00", "R1", 1, 999),
]


def _ch_test_client():
    return clickhouse_connect.get_client(
        host="localhost",
        port=int(os.environ.get("CLICKHOUSE_TEST_PORT", "8124")),
        username="transit",
        password="transit",
        database="transit_test",
    )


@pytest.mark.skipif(os.environ.get("RUN_CH_INTEGRATION") != "1", reason="requires `make ch-test`")
def test_dedup_ch_picks_latest_captured_at_then_file_name_tiebreak():
    from db.clickhouse.bootstrap import apply_schema

    client = _ch_test_client()
    client.command("DROP TABLE IF EXISTS updates")
    apply_schema(client)
    client.insert(
        "updates",
        FIXTURE_ROWS,
        column_names=[
            "agency_id",
            "captured_at",
            "file_name",
            "trip_id",
            "service_type",
            "scheduled_time",
            "route_code",
            "stop_sequence",
            "dep_delay",
        ],
    )

    sql = build_dedup_ch_sql()
    result = client.query(sql, parameters={"agency_id": 1})
    rows = result.result_rows

    assert len(rows) == 1  # one group: (R1, weekday, 10:00, T1, 2026-01-01, stop 1)
    # dep_delay column is last in the SELECT list build_dedup_inner_sql/build_dedup_ch_sql produce.
    assert rows[0][-1] == 120  # the 10:05 row wins — latest captured_at, not the file_name tiebreak
    client.close()


@pytest.mark.skipif(os.environ.get("RUN_CH_INTEGRATION") != "1", reason="requires `make ch-test`")
def test_dedup_ch_buckets_by_jst_day_not_utc_day():
    """Guards the JST/UTC bug called out in the design doc's Risks section:
    a captured_at of 2026-01-01 20:00 UTC is 2026-01-02 05:00 JST — grouping
    by bare UTC toDate() would put this row in the wrong day's dedup group."""
    from db.clickhouse.bootstrap import apply_schema

    client = _ch_test_client()
    client.command("DROP TABLE IF EXISTS updates")
    apply_schema(client)
    client.insert(
        "updates",
        [(1, datetime(2026, 1, 1, 20, 0, 0, tzinfo=timezone.utc), "a/1.pb", "T1", "weekday", "10:00", "R1", 1, 30)],
        column_names=[
            "agency_id",
            "captured_at",
            "file_name",
            "trip_id",
            "service_type",
            "scheduled_time",
            "route_code",
            "stop_sequence",
            "dep_delay",
        ],
    )
    sql = build_dedup_ch_sql()
    result = client.query(sql, parameters={"agency_id": 1})
    rows = result.result_rows
    assert len(rows) == 1
    date_col_index = 4  # route_code, service_type, scheduled_time, trip_id, date, ...
    assert rows[0][date_col_index] == datetime(2026, 1, 2).date()  # JST day, not the UTC day (01-01)
    client.close()


@pytest.mark.skipif(os.environ.get("RUN_CH_INTEGRATION") != "1", reason="requires `make ch-test`")
def test_dedup_ch_tiebreak_uses_file_name_when_captured_at_ties():
    from db.clickhouse.bootstrap import apply_schema

    client = _ch_test_client()
    client.command("DROP TABLE IF EXISTS updates")
    apply_schema(client)
    # Only the two tied-captured_at rows this time.
    client.insert(
        "updates",
        FIXTURE_ROWS[:2],
        column_names=[
            "agency_id",
            "captured_at",
            "file_name",
            "trip_id",
            "service_type",
            "scheduled_time",
            "route_code",
            "stop_sequence",
            "dep_delay",
        ],
    )
    sql = build_dedup_ch_sql()
    result = client.query(sql, parameters={"agency_id": 1})
    rows = result.result_rows
    assert len(rows) == 1
    assert rows[0][-1] == 90  # file_name "a/000002.pb" > "a/000001.pb" wins the tie
    client.close()
