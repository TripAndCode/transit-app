"""Proves build_dedup_ch_sql selects the same "latest observation per stop
event" rows as the Postgres DISTINCT ON version it replaced, on a small
fixture designed to exercise the tiebreak (two files, same captured_at
second, different file_name).

Also covers the pieces that used to be pinned against the now-deleted
Postgres builders (`build_dedup_inner_sql` / `_dedup_cte`) in
tests/pipeline/test_dedup.py: the implausible-delay clamp, the
`include_captured_at` projection toggle, and `_dedup_cte_ch` — the
ClickHouse-dialect composition of a range filter + dedup that every
report/route/overview helper reads live `updates` through."""

import os
from datetime import date, datetime, timezone

import clickhouse_connect
import pytest

from pipeline.db import MAX_PLAUSIBLE_DELAY_SEC, build_dedup_ch_sql

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


def test_include_captured_at_flag_adds_last_captured_at_projection():
    """include_captured_at=True surfaces `last_captured_at` (used by
    agg_route_daily's last_seen_at, see pipeline/analyze.py); default keeps
    only the deduped `date`. Pure string check — no ClickHouse instance
    needed. Ports tests/pipeline/test_dedup.py's equivalent assertion
    against the now-deleted `build_dedup_inner_sql`."""
    assert "last_captured_at" in build_dedup_ch_sql(include_captured_at=True)
    assert "last_captured_at" not in build_dedup_ch_sql()
    # both still project the same deduped date
    assert "AS date" in build_dedup_ch_sql()


@pytest.mark.skipif(os.environ.get("RUN_CH_INTEGRATION") != "1", reason="requires `make ch-test`")
def test_dedup_ch_excludes_implausible_delay_spikes():
    """The dedup drops |dep_delay| > MAX_PLAUSIBLE_DELAY_SEC (frozen-feed
    spikes), so every aggregate/report built from it is protected — not just
    the heatmap. Regression for the 2026-06-07 馬木料金所前 false average (see
    pipeline/db.py's MAX_PLAUSIBLE_DELAY_SEC docstring). Ports
    tests/pipeline/test_dedup.py's equivalent assertion against the
    now-deleted Postgres `build_dedup_inner_sql`."""
    from db.clickhouse.bootstrap import apply_schema
    from pipeline.clickhouse import insert_updates

    client = _ch_test_client()
    client.command("DROP TABLE IF EXISTS updates")
    apply_schema(client)
    insert_updates(
        client,
        1,
        [
            # one plausible trip and one frozen-feed spike trip, same day
            ("ok.pb", datetime(2026, 1, 1, 3, 0, 0, tzinfo=timezone.utc), "trip_ok", "weekday", "10:00", "R1", 1, 180),
            (
                "spike.pb",
                datetime(2026, 1, 1, 3, 5, 0, tzinfo=timezone.utc),
                "trip_spike",
                "weekday",
                "10:05",
                "R1",
                2,
                MAX_PLAUSIBLE_DELAY_SEC + 1,
            ),
        ],
    )
    sql = build_dedup_ch_sql()
    result = client.query(sql, parameters={"agency_id": 1})
    delays = sorted(r[-1] for r in result.result_rows)
    assert delays == [180], f"spike should be excluded; got {delays}"
    client.close()


@pytest.mark.skipif(os.environ.get("RUN_CH_INTEGRATION") != "1", reason="requires `make ch-test`")
def test_dedup_cte_ch_picks_latest_observation():
    """`pipeline.reports.filters._dedup_cte_ch` composes
    `build_updates_filter_ch`'s WHERE fragment with `build_dedup_ch_sql`'s
    dedup — every report/route/overview helper reads live `updates` through
    this one wrapper. Ports tests/pipeline/test_dedup.py's equivalent
    end-to-end assertion against the now-deleted asyncpg `_dedup_cte`.

    Same trick as the retired Postgres test: dep_delay is NOT monotone with
    captured_at — the worst delay (300s) sits on the EARLIEST row and the
    final delay (120s) sits on the latest. MAX(dep_delay) would pick 300;
    latest-by-captured_at picks 120."""
    from api.range import RangeCtx
    from db.clickhouse.bootstrap import apply_schema
    from pipeline.clickhouse import insert_updates
    from pipeline.reports.filters import _dedup_cte_ch

    client = _ch_test_client()
    client.command("DROP TABLE IF EXISTS updates")
    apply_schema(client)
    day = date(2026, 1, 1)
    # UTC hours chosen mid-day so the JST-shifted civil date stays 2026-01-01
    # on both sides of the toDate(..., 'Asia/Tokyo') cast.
    t0 = datetime(2026, 1, 1, 3, 0, 0, tzinfo=timezone.utc)
    t1 = datetime(2026, 1, 1, 3, 1, 0, tzinfo=timezone.utc)
    t2 = datetime(2026, 1, 1, 3, 2, 0, tzinfo=timezone.utc)
    insert_updates(
        client,
        1,
        [
            ("t_minus_120.pb", t0, "trip_a", "weekday", "10:00", "R1", 1, 300),
            ("t_minus_60.pb", t1, "trip_a", "weekday", "10:00", "R1", 1, 60),
            ("t.pb", t2, "trip_a", "weekday", "10:00", "R1", 1, 120),
        ],
    )
    ctx = RangeCtx(from_date=day, to_date=day)
    cte_sql, params = _dedup_cte_ch(ctx)
    result = client.query(
        f"WITH {cte_sql} SELECT dep_delay FROM deduped",
        parameters={"agency_id": 1, **params},
    )
    rows = result.result_rows
    assert len(rows) == 1
    assert rows[0][0] == 120, f"expected latest (120s), got {rows[0][0]} (would be 300 under old MAX semantics)"
    client.close()


def test_delay_ceiling_is_single_source_of_truth():
    """analyze.py must reuse db.py's constant, not redefine its own (drift
    guard). Moved from tests/pipeline/test_dedup.py — unrelated to the
    deleted Postgres builders, but that was the constant's previous home."""
    import pipeline.analyze as analyze
    import pipeline.db as db

    assert analyze.MAX_PLAUSIBLE_DELAY_SEC is db.MAX_PLAUSIBLE_DELAY_SEC
