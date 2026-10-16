"""The ClickHouse-computed aggregates reproduce the Postgres builders they replaced.

Each test seeds observations, runs analyze, and compares what Postgres now holds
for every ClickHouse-computed table with what the frozen legacy builder
(tests/pipeline/ch_parity.py) computes from the same observations.
"""

import pathlib
from datetime import date
from decimal import Decimal

import pytest

from pipeline import ch_aggregates
from pipeline.analyze import analyze
from pipeline.clickhouse import insert_updates
from pipeline.static_loader import load_static
from pipeline.strategies import static_join
from tests.fixtures.synthetic_gtfs import ALL_PATTERNS, run_pattern
from tests.pipeline.ch_agg_helpers import edge_rows
from tests.pipeline.ch_parity import legacy_rows, stored_rows

FIXTURES = pathlib.Path(__file__).parent.parent / "fixtures"


def _assert_parity(pg_conn, ch_client, agency_id: int) -> None:
    for table in ch_aggregates.TABLES:
        stored = stored_rows(pg_conn, agency_id, table)
        assert stored, f"{table}: nothing stored, so this would pass vacuously"
        assert stored == legacy_rows(pg_conn, ch_client, agency_id, table), table


@pytest.mark.parametrize("pattern_fn", ALL_PATTERNS, ids=lambda fn: fn.__name__)
def test_parity_on_the_synthetic_patterns(pattern_fn, tmp_path, pg_conn, agency_id, ch_client):
    run_pattern(pattern_fn(), tmp_path, pg_conn, agency_id, ch_client)
    _assert_parity(pg_conn, ch_client, agency_id)


def test_parity_on_the_edge_cases(pg_conn, agency_id, ch_client):
    insert_updates(ch_client, agency_id, edge_rows())
    analyze(agency_id, pg_conn, ch_client)
    _assert_parity(pg_conn, ch_client, agency_id)


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
    _assert_parity(pg_conn, ch_client, agency_id)


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
