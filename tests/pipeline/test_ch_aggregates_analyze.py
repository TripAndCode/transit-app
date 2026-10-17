"""analyze builds the ClickHouse-computed aggregates and keeps the Postgres copy in step."""

from datetime import date, datetime

import pytest

from pipeline import analyze as analyze_mod
from pipeline import ch_aggregates
from pipeline.analyze import analyze
from pipeline.clickhouse import insert_updates
from tests.fixtures.synthetic_gtfs import ALL_PATTERNS, run_pattern
from tests.pipeline.ch_agg_helpers import ch_rows, pg_rows, record_builds, update_row, work_tables
from tests.pipeline.ch_parity import legacy_rows, stored_rows

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


def test_a_table_clickhouse_lost_is_rebuilt_whole_without_rebuilding_the_agency(
    pg_conn, agency_id, ch_client, monkeypatch
):
    """A table that has just moved starts empty in ClickHouse while its Postgres
    copy holds the legacy rows. Only that table is rebuilt whole."""
    insert_updates(
        ch_client,
        agency_id,
        [update_row(datetime(2026, 9, 14, 8, 0), "a", 60), update_row(datetime(2026, 10, 2, 8, 0), "b", 120)],
    )
    analyze(agency_id, pg_conn, ch_client)
    served = {table: pg_rows(pg_conn, agency_id, table) for table in ch_aggregates.TABLES}
    for table in ch_aggregates.TABLES:
        ch_client.command(f"TRUNCATE TABLE {table}")
    built = record_builds(monkeypatch)

    analyze(agency_id, pg_conn, ch_client)  # the ledger says nothing changed

    assert built == [(table, [SEPT, OCT]) for table in ch_aggregates.TABLES]
    assert "alltime: ClickHouse scan" not in analyze_mod._step_ms, "the whole agency was rebuilt"
    for table in ch_aggregates.TABLES:
        assert ch_rows(ch_client, agency_id, table) == pg_rows(pg_conn, agency_id, table) == served[table], table


def test_a_table_the_agency_has_no_rows_for_is_not_rebuilt_on_every_run(pg_conn, agency_id, ch_client, monkeypatch):
    insert_updates(ch_client, agency_id, [update_row(datetime(2026, 9, 14, 8, 0), "a", 60, scheduled_time=None)])
    analyze(agency_id, pg_conn, ch_client)
    assert pg_rows(pg_conn, agency_id, "agg_hour_daily") == []
    built = record_builds(monkeypatch)
    analyze(agency_id, pg_conn, ch_client)
    assert built == []


def test_a_failed_build_leaves_both_stores_as_they_stood_and_no_work_table(pg_conn, agency_id, ch_client, monkeypatch):
    """The last table's build fails, after every earlier table's has finished:
    nothing was swapped, so every table stands in both stores."""
    insert_updates(ch_client, agency_id, [update_row(datetime(2026, 9, 14, 8, 0), "a", 60)])
    analyze(agency_id, pg_conn, ch_client)
    before = {t: (ch_rows(ch_client, agency_id, t), pg_rows(pg_conn, agency_id, t)) for t in ch_aggregates.TABLES}
    insert_updates(ch_client, agency_id, [update_row(datetime(2026, 9, 15, 8, 0), "c", 180)])
    real = ch_aggregates.select_sql

    def select_sql(table, month, days=None, source=None):
        if table == ch_aggregates.TABLES[-1]:
            raise RuntimeError("injected build failure")
        return real(table, month, days, source)

    monkeypatch.setattr(ch_aggregates, "select_sql", select_sql)
    with pytest.raises(RuntimeError, match="injected build failure"):
        analyze(agency_id, pg_conn, ch_client)

    after = {t: (ch_rows(ch_client, agency_id, t), pg_rows(pg_conn, agency_id, t)) for t in ch_aggregates.TABLES}
    assert after == before
    assert work_tables(ch_client) == []


def test_a_postgres_copy_that_disagrees_with_clickhouse_rolls_the_run_back(pg_conn, agency_id, ch_client, monkeypatch):
    insert_updates(ch_client, agency_id, [update_row(datetime(2026, 9, 14, 8, 0), "a", 60)])
    analyze(agency_id, pg_conn, ch_client)
    before = {table: pg_rows(pg_conn, agency_id, table) for table in ch_aggregates.TABLES}
    insert_updates(ch_client, agency_id, [update_row(datetime(2026, 9, 15, 8, 0), "c", 180)])
    real = ch_aggregates.rows_sql

    def rows_sql(table, agency, months):
        sql, params = real(table, agency, months)
        return sql + " LIMIT 0", params

    monkeypatch.setattr(ch_aggregates, "rows_sql", rows_sql)
    with pytest.raises(RuntimeError, match="does not match ClickHouse"):
        analyze(agency_id, pg_conn, ch_client)
    assert {table: pg_rows(pg_conn, agency_id, table) for table in ch_aggregates.TABLES} == before

    monkeypatch.undo()
    analyze(agency_id, pg_conn, ch_client)  # the next run brings the two stores back together
    for table in ch_aggregates.TABLES:
        assert ch_rows(ch_client, agency_id, table) == pg_rows(pg_conn, agency_id, table) != before[table], table


def test_an_incremental_run_recomputes_only_the_changed_dates(pg_conn, agency_id, ch_client, monkeypatch):
    insert_updates(
        ch_client,
        agency_id,
        [update_row(datetime(2026, 9, 14, 8, 0), "a", 60), update_row(datetime(2026, 10, 2, 8, 0), "b", 120)],
    )
    analyze(agency_id, pg_conn, ch_client)
    calls = []
    real = ch_aggregates.select_sql

    def select_sql(table, month, days=None, source=None):
        calls.append((table, month, days))
        return real(table, month, days, source)

    monkeypatch.setattr(ch_aggregates, "select_sql", select_sql)
    insert_updates(ch_client, agency_id, [update_row(datetime(2026, 10, 3, 8, 0), "c", 180)])
    analyze(agency_id, pg_conn, ch_client)

    assert calls == [(table, OCT, [date(2026, 10, 3)]) for table in ch_aggregates.TABLES]
    for table in ch_aggregates.TABLES:
        assert stored_rows(pg_conn, agency_id, table) == legacy_rows(pg_conn, ch_client, agency_id, table), table
        assert ch_rows(ch_client, agency_id, table) == pg_rows(pg_conn, agency_id, table), table


def test_a_rebuild_times_the_shared_dedup_apart_from_each_tables_build(pg_conn, agency_id, ch_client):
    insert_updates(ch_client, agency_id, [update_row(datetime(2026, 9, 14, 8, 0), "a", 60)])
    analyze(agency_id, pg_conn, ch_client)
    assert "ClickHouse dedup" in analyze_mod._step_ms
    for table in ch_aggregates.TABLES:
        assert f"{table}: ClickHouse build" in analyze_mod._step_ms, table
