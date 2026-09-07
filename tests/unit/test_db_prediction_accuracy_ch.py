"""Tests for pipeline.db.build_prediction_accuracy_ch_sql.

Mirrors tests/unit/test_db_dedup_ch.py's split: a pure string-shape test that
runs everywhere, plus a ClickHouse-integration test gated behind
RUN_CH_INTEGRATION=1 (see `make ch-test` / transit-app-gotchas)."""

import os
from datetime import datetime, timezone

import clickhouse_connect
import pytest

from pipeline.db import MAX_PLAUSIBLE_DELAY_SEC, build_prediction_accuracy_ch_sql
from pipeline.prediction_accuracy import rows_to_lead_bucket_stats


def test_build_prediction_accuracy_ch_sql_shape():
    """Pure string check -- no ClickHouse instance needed."""
    sql = build_prediction_accuracy_ch_sql()
    assert "groupArray(tuple(u.captured_at, u.dep_delay)) AS observations" in sql
    assert "argMax(u.dep_delay, (u.captured_at, u.file_name)) AS final_dep_delay" in sql
    assert "HAVING count() > 1" in sql
    assert "{agency_id:UInt16}" in sql
    assert str(MAX_PLAUSIBLE_DELAY_SEC) in sql


def test_build_prediction_accuracy_ch_sql_extra_where_composes():
    sql = build_prediction_accuracy_ch_sql(extra_where="u.route_code = 'R1'")
    assert "AND (u.route_code = 'R1')" in sql


def _ch_test_client():
    return clickhouse_connect.get_client(
        host="localhost",
        port=int(os.environ.get("CLICKHOUSE_TEST_PORT", "8124")),
        username="transit",
        password="transit",
        database="transit_test",
    )


@pytest.mark.skipif(os.environ.get("RUN_CH_INTEGRATION") != "1", reason="requires `make ch-test`")
def test_prediction_accuracy_ch_sql_end_to_end():
    """A stop event observed 3 times (2 early, 1 final) round-trips through
    ClickHouse and produces the expected bucketed error -- the same
    known-fixture shape tests/unit/test_prediction_accuracy.py exercises
    purely in Python, now proven against a real query."""
    from db.clickhouse.bootstrap import apply_schema
    from pipeline.clickhouse import insert_updates

    client = _ch_test_client()
    client.command("DROP TABLE IF EXISTS updates")
    apply_schema(client)
    insert_updates(
        client,
        1,
        [
            # (file_name, captured_at, trip_id, service_type, scheduled_time, route_code, stop_sequence, dep_delay)
            ("a.pb", datetime(2026, 4, 1, 8, 39, 0, tzinfo=timezone.utc), "T1", "weekday", "09:00", "R1", 1, 600),
            ("b.pb", datetime(2026, 4, 1, 8, 55, 0, tzinfo=timezone.utc), "T1", "weekday", "09:00", "R1", 1, 60),
            ("c.pb", datetime(2026, 4, 1, 8, 58, 0, tzinfo=timezone.utc), "T1", "weekday", "09:00", "R1", 1, 120),
            # A different trip observed only once must be excluded (HAVING count() > 1).
            ("d.pb", datetime(2026, 4, 1, 8, 58, 0, tzinfo=timezone.utc), "T2", "weekday", "09:05", "R1", 1, 30),
        ],
    )
    sql = build_prediction_accuracy_ch_sql()
    result = client.query(sql, parameters={"agency_id": 1})
    rows = result.result_rows

    assert len(rows) == 1  # only T1's stop event has >1 observation
    assert rows[0][6] == 120  # final_dep_delay: the latest (08:58) observation
    stats = rows_to_lead_bucket_stats(rows)
    total_samples = sum(r["samples"] for r in stats)
    assert total_samples == 2  # 2 early observations (a.pb, b.pb), c.pb excluded as final
    client.close()
