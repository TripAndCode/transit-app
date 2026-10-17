"""The ClickHouse-computed aggregates reproduce the Postgres builders they replaced.

Each test seeds observations, runs analyze, and compares what Postgres now holds
for every ClickHouse-computed table with what the frozen legacy builder
(tests/pipeline/ch_parity.py) computes from the same observations.
"""

import pathlib
from datetime import date, datetime
from decimal import Decimal

import pytest

from pipeline import ch_aggregates
from pipeline.analyze import analyze
from pipeline.clickhouse import insert_updates
from pipeline.histogram import N_BUCKETS, bucketize
from pipeline.static_loader import load_static
from pipeline.strategies import static_join
from tests.fixtures.synthetic_gtfs import ALL_PATTERNS, run_pattern
from tests.pipeline.ch_agg_helpers import JST, edge_rows, histogram_edge_rows, whole_history_rows
from tests.pipeline.ch_parity import legacy_rows, stored_rows

FIXTURES = pathlib.Path(__file__).parent.parent / "fixtures"


def _assert_parity(pg_conn, ch_client, agency_id: int, *, static: bool) -> None:
    for table in ch_aggregates.COPIED_TABLES:
        stored = stored_rows(pg_conn, agency_id, table)
        if static or table not in ch_aggregates.STATIC_JOINED_TABLES:
            assert stored, f"{table}: nothing stored, so this would pass vacuously"
        assert stored == legacy_rows(pg_conn, ch_client, agency_id, table), table


@pytest.mark.parametrize("pattern_fn", ALL_PATTERNS, ids=lambda fn: fn.__name__)
def test_parity_on_the_synthetic_patterns(pattern_fn, tmp_path, pg_conn, agency_id, ch_client):
    run_pattern(pattern_fn(), tmp_path, pg_conn, agency_id, ch_client)
    _assert_parity(pg_conn, ch_client, agency_id, static=True)


def test_parity_on_the_edge_cases(pg_conn, agency_id, ch_client):
    insert_updates(ch_client, agency_id, edge_rows())
    analyze(agency_id, pg_conn, ch_client)
    _assert_parity(pg_conn, ch_client, agency_id, static=False)


def test_parity_on_a_real_feed(pg_conn, ch_client):
    with pg_conn.cursor() as cur:
        cur.execute(
            "INSERT INTO agencies (agency_name, feed_url, ingest_strategy) "
            "VALUES (%s, %s, 'static_join') RETURNING agency_id",
            ("広島電鉄_parity", "https://ajt-mobusta-gtfs.mcapps.jp/realtime/8/trip_updates.bin"),
        )
        agency_id = cur.fetchone()[0]
    pg_conn.commit()
    load_static(str(FIXTURES / "hiroden_static.zip"), agency_id, pg_conn)
    raw = (FIXTURES / "hiroden_tu.bin").read_bytes()
    for i, captured_at in enumerate(("2026-05-09T12:00:00+09:00", "2026-05-09T12:00:30+09:00")):
        rows = static_join.parse_feed(raw, captured_at, f"parity/{i}.bin", agency_id, pg_conn)
        insert_updates(ch_client, agency_id, rows)
    analyze(agency_id, pg_conn, ch_client)
    _assert_parity(pg_conn, ch_client, agency_id, static=True)


def test_parity_on_the_whole_history_rows(pg_conn, agency_id, ch_client):
    insert_updates(ch_client, agency_id, whole_history_rows())
    analyze(agency_id, pg_conn, ch_client)
    _assert_parity(pg_conn, ch_client, agency_id, static=False)


def test_agg_hour_daily_rounds_mean_ties_away_from_zero(pg_conn, agency_id, ch_client):
    insert_updates(ch_client, agency_id, edge_rows())
    analyze(agency_id, pg_conn, ch_client)
    with pg_conn.cursor() as cur:
        cur.execute(
            "SELECT hour, avg_min FROM agg_hour_daily WHERE agency_id = %s AND date = %s",
            (agency_id, date(2026, 9, 14)),
        )
        assert dict(cur.fetchall()) == {
            0: Decimal("0.50"),
            3: Decimal("0.75"),
            7: Decimal("0.03"),
            9: Decimal("-0.03"),
            10: Decimal("1.00"),
            23: Decimal("-0.50"),
        }


def test_route_daily_and_trend_match_hand_computed_values_in_both_stores(pg_conn, agency_id, ch_client):
    insert_updates(ch_client, agency_id, edge_rows())
    analyze(agency_id, pg_conn, ch_client)

    daily = (
        "SELECT route_code, service_type, avg_delay_sec, worst_delay_sec, trips_observed, samples, sum_delay_sec "
        "FROM agg_route_daily WHERE agency_id = {} AND date = '2026-09-14'"
    )
    expected_daily = {
        ("R1", "平日"): (141, 600, 5, 5, 705),
        ("R1", ""): (90, 90, 1, 1, 90),
        ("r1", "平日"): (120, 120, 1, 1, 120),
        ("TIE+", "平日"): (2, 2, 2, 2, 3),
        ("TIE-", "平日"): (-2, -1, 2, 2, -3),
    }
    trend = (
        "SELECT route_code, service_type, avg_min, sum_late_sec "
        "FROM agg_daily_trend WHERE agency_id = {} AND date = '2026-09-14'"
    )
    expected_trend = {
        ("R1", "平日"): (2.35, 735),
        ("R1", ""): (1.5, 90),
        ("r1", "平日"): (2.0, 120),
        ("TIE+", "平日"): (0.03, 3),
        ("TIE-", "平日"): (-0.03, 0),
    }
    with pg_conn.cursor() as cur:
        cur.execute(daily.format("%s"), (agency_id,))
        pg_daily = cur.fetchall()
        cur.execute(trend.format("%s"), (agency_id,))
        pg_trend = cur.fetchall()
        cur.execute(
            "SELECT last_seen_at FROM agg_route_daily "
            "WHERE agency_id = %s AND route_code = 'R1' AND service_type = '平日'",
            (agency_id,),
        )
        assert cur.fetchone()[0] == datetime(2026, 9, 15, 3, 30, tzinfo=JST)
    ch_daily = ch_client.query(daily.format("{agency_id:Int32}"), parameters={"agency_id": agency_id}).result_rows
    ch_trend = ch_client.query(trend.format("{agency_id:Int32}"), parameters={"agency_id": agency_id}).result_rows

    for rows in (pg_daily, ch_daily):
        assert {(r[0], r[1]): tuple(r[2:]) for r in rows} == expected_daily
    for rows in (pg_trend, ch_trend):
        got = {(r[0], r[1]): (float(r[2]), r[3]) for r in rows}
        assert got.keys() == expected_trend.keys()
        for key, (avg_min, late) in expected_trend.items():
            assert got[key] == (pytest.approx(avg_min, abs=1e-6), late), key


def test_route_daily_dist_bins_the_bucket_edges_like_postgres(pg_conn, agency_id, ch_client):
    rows = histogram_edge_rows()
    insert_updates(ch_client, agency_id, rows)
    analyze(agency_id, pg_conn, ch_client)
    _assert_parity(pg_conn, ch_client, agency_id, static=False)

    kept = [row[7] for row in rows if abs(row[7]) <= 7200]
    expected = [0] * N_BUCKETS
    for delay in kept:
        expected[bucketize(delay)] += 1
    query = (
        "SELECT samples, sum_delay_sec, on_time_count, late5_count, hist FROM agg_route_daily_dist "
        "WHERE agency_id = {} AND route_code = 'EDGE'"
    )
    with pg_conn.cursor() as cur:
        cur.execute(query.format("%s"), (agency_id,))
        pg_row = cur.fetchone()
    (ch_row,) = ch_client.query(query.format("{agency_id:Int32}"), parameters={"agency_id": agency_id}).result_rows
    for row in (pg_row, ch_row):
        samples, total, on_time, late5, hist = row
        assert (samples, total) == (len(kept), sum(kept))
        assert on_time == sum(1 for delay in kept if delay <= 60)
        assert late5 == sum(1 for delay in kept if delay > 300)
        assert list(hist) == expected
