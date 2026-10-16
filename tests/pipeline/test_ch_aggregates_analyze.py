"""analyze builds the ClickHouse-computed aggregates and keeps the Postgres copy in step."""

from datetime import date, datetime

import pytest

from pipeline import ch_aggregates
from pipeline.analyze import analyze
from pipeline.clickhouse import insert_updates
from tests.fixtures.synthetic_gtfs import ALL_PATTERNS, run_pattern
from tests.pipeline.ch_agg_helpers import ch_rows, pg_rows, record_builds, update_row, work_tables

SEPT = 202609
OCT = 202610


def _dates(ch_client, pg_conn, agency_id: int, table: str) -> tuple[list[date], list[date]]:
    """The distinct dates *table* holds for the agency: (ClickHouse's, Postgres's)."""
    ch = ch_client.query(
        f"SELECT DISTINCT date FROM {table} WHERE agency_id = {{agency_id:Int32}} ORDER BY date",
        parameters={"agency_id": agency_id},
    ).result_rows
    with pg_conn.cursor() as cur:
        cur.execute(f"SELECT DISTINCT date FROM {table} WHERE agency_id = %s ORDER BY date", (agency_id,))
        pg = cur.fetchall()
    return [row[0] for row in ch], [row[0] for row in pg]


@pytest.mark.parametrize("pattern_fn", ALL_PATTERNS, ids=lambda fn: fn.__name__)
def test_clickhouse_holds_the_rows_postgres_serves(pattern_fn, tmp_path, pg_conn, agency_id, ch_client):
    run_pattern(pattern_fn(), tmp_path, pg_conn, agency_id, ch_client)
    for table in ch_aggregates.TABLES:
        served = pg_rows(pg_conn, agency_id, table)
        assert served, f"{table}: nothing stored, so this would pass vacuously"
        assert ch_rows(ch_client, agency_id, table) == served, table


def test_a_changed_date_rebuilds_only_the_month_holding_it(pg_conn, agency_id, ch_client, monkeypatch):
    insert_updates(
        ch_client,
        agency_id,
        [update_row(datetime(2026, 9, 14, 8, 0), "a", 60), update_row(datetime(2026, 10, 2, 8, 0), "b", 120)],
    )
    analyze(agency_id, pg_conn, ch_client)
    built = record_builds(monkeypatch)
    insert_updates(ch_client, agency_id, [update_row(datetime(2026, 10, 3, 8, 0), "c", 180)])
    analyze(agency_id, pg_conn, ch_client)

    assert built == [(table, [OCT]) for table in ch_aggregates.TABLES]
    every_date = [date(2026, 9, 14), date(2026, 10, 2), date(2026, 10, 3)]
    for table in ch_aggregates.TABLES:
        assert _dates(ch_client, pg_conn, agency_id, table) == (every_date, every_date), table
        assert ch_rows(ch_client, agency_id, table) == pg_rows(pg_conn, agency_id, table), table
    assert work_tables(ch_client) == []


def test_a_month_whose_rows_are_gone_is_emptied_in_both_stores(pg_conn, agency_id, ch_client):
    september = update_row(datetime(2026, 9, 14, 8, 0), "a", 60)
    insert_updates(ch_client, agency_id, [september, update_row(datetime(2026, 10, 2, 8, 0), "b", 120)])
    analyze(agency_id, pg_conn, ch_client)
    ch_client.command("TRUNCATE TABLE updates")
    insert_updates(ch_client, agency_id, [september])
    analyze(agency_id, pg_conn, ch_client)
    for table in ch_aggregates.TABLES:
        assert _dates(ch_client, pg_conn, agency_id, table) == ([date(2026, 9, 14)], [date(2026, 9, 14)]), table
