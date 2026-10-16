"""The ClickHouse side of the moved aggregates, against a real ClickHouse.

pipeline.analyze drives these primitives; here they run on their own, so each
behavior is pinned without a Postgres copy in the way.
"""

from datetime import date, datetime
from decimal import Decimal

import pytest

from pipeline import ch_aggregates
from pipeline.clickhouse import insert_updates
from tests.pipeline.ch_agg_helpers import update_row, work_tables

AGENCY = 7001
OTHER = 7002
AUG = 202608
SEPT = 202609
OCT = 202610
WORK = ch_aggregates.work_table("agg_hour_daily", AGENCY)


def _hour_daily(ch_client, table: str, agency_id: int = AGENCY) -> list[tuple]:
    """(date, hour, avg_min, samples, sum_delay_sec) for *agency_id* in *table*, sorted."""
    return sorted(
        ch_client.query(
            f"SELECT date, hour, avg_min, samples, sum_delay_sec FROM {table} WHERE agency_id = {{agency_id:Int32}}",
            parameters={"agency_id": agency_id},
        ).result_rows
    )


def _rebuild(ch_client, agency_id: int, months: list[int]) -> None:
    ch_aggregates.build(ch_client, "agg_hour_daily", agency_id, months)
    ch_aggregates.swap(ch_client, "agg_hour_daily", agency_id, months)


def test_each_clickhouse_aggregate_has_the_registry_columns_and_month_partitions(ch_client):
    for table in ch_aggregates.TABLES:
        columns = ch_client.query(
            "SELECT name FROM system.columns WHERE database = currentDatabase() AND table = {t:String} "
            "ORDER BY position",
            parameters={"t": table},
        ).result_rows
        assert [row[0] for row in columns] == list(ch_aggregates.COLUMNS[table]), table
        ((key,),) = ch_client.query(
            "SELECT partition_key FROM system.tables WHERE database = currentDatabase() AND name = {t:String}",
            parameters={"t": table},
        ).result_rows
        assert "agency_id" in key and "toYYYYMM(date)" in key, (table, key)


def test_each_clickhouse_aggregate_names_only_columns_its_postgres_table_has(pg_conn):
    with pg_conn.cursor() as cur:
        for table in ch_aggregates.TABLES:
            cur.execute("SELECT column_name FROM information_schema.columns WHERE table_name = %s", (table,))
            assert set(ch_aggregates.COLUMNS[table]) <= {row[0] for row in cur.fetchall()}, table


def test_build_computes_only_the_requested_month_and_rounds_ties_away_from_zero(ch_client):
    day = datetime(2026, 9, 14, 8, 0)
    insert_updates(
        ch_client,
        AGENCY,
        [
            update_row(day, "a", 1, scheduled_time="07:10:00"),
            update_row(day, "b", 2, scheduled_time="07:20"),
            update_row(day, "c", -1, scheduled_time="09:00:00"),
            update_row(day, "d", -2, scheduled_time="09:30:00"),
            update_row(datetime(2026, 10, 2, 8, 0), "e", 60, scheduled_time="08:00:00"),
        ],
    )
    ch_aggregates.build(ch_client, "agg_hour_daily", AGENCY, [SEPT])
    assert _hour_daily(ch_client, WORK) == [
        (date(2026, 9, 14), 7, Decimal("0.03"), 2, 3),
        (date(2026, 9, 14), 9, Decimal("-0.03"), 2, -3),
    ]
    assert _hour_daily(ch_client, "agg_hour_daily") == []


def test_a_row_before_the_cutover_on_the_first_belongs_to_the_previous_month(ch_client):
    """Service day 2026-10-01 starts at 04:00 JST, so 03:30 is still 2026-09-30."""
    insert_updates(ch_client, AGENCY, [update_row(datetime(2026, 10, 1, 3, 30), "a", 120, scheduled_time="03:30:00")])
    ch_aggregates.build(ch_client, "agg_hour_daily", AGENCY, [OCT])
    assert _hour_daily(ch_client, WORK) == []
    ch_aggregates.build(ch_client, "agg_hour_daily", AGENCY, [SEPT])
    assert _hour_daily(ch_client, WORK) == [(date(2026, 9, 30), 3, Decimal("2.00"), 1, 120)]


def test_build_starts_from_an_empty_work_table_whatever_a_dead_run_left(ch_client):
    ch_client.command(f"CREATE TABLE {WORK} AS agg_hour_daily")
    ch_client.command(f"INSERT INTO {WORK} VALUES ({AGENCY}, '2026-09-01', 5, 9.99, 1, 599)")
    insert_updates(ch_client, AGENCY, [update_row(datetime(2026, 9, 14, 8, 0), "a", 60, scheduled_time="08:00:00")])
    ch_aggregates.build(ch_client, "agg_hour_daily", AGENCY, [SEPT])
    assert _hour_daily(ch_client, WORK) == [(date(2026, 9, 14), 8, Decimal("1.00"), 1, 60)]


def test_swap_replaces_the_built_months_and_empties_the_ones_with_no_rows_left(ch_client):
    insert_updates(ch_client, OTHER, [update_row(datetime(2026, 9, 14, 8, 0), "x", 30, scheduled_time="08:00:00")])
    _rebuild(ch_client, OTHER, [SEPT])
    insert_updates(
        ch_client,
        AGENCY,
        [
            update_row(datetime(2026, 9, 14, 8, 0), "a", 60, scheduled_time="08:00:00"),
            update_row(datetime(2026, 10, 2, 8, 0), "b", 120, scheduled_time="08:00:00"),
        ],
    )
    _rebuild(ch_client, AGENCY, [SEPT, OCT])
    assert _hour_daily(ch_client, "agg_hour_daily") == [
        (date(2026, 9, 14), 8, Decimal("1.00"), 1, 60),
        (date(2026, 10, 2), 8, Decimal("2.00"), 1, 120),
    ]

    ch_client.command("TRUNCATE TABLE updates")
    insert_updates(ch_client, AGENCY, [update_row(datetime(2026, 9, 14, 8, 0), "a", 90, scheduled_time="08:00:00")])
    _rebuild(ch_client, AGENCY, [SEPT, OCT])
    assert _hour_daily(ch_client, "agg_hour_daily") == [(date(2026, 9, 14), 8, Decimal("1.50"), 1, 90)]
    assert _hour_daily(ch_client, "agg_hour_daily", OTHER) == [(date(2026, 9, 14), 8, Decimal("0.50"), 1, 30)]


def test_swap_refuses_a_work_table_holding_months_outside_the_rebuild(ch_client):
    insert_updates(ch_client, AGENCY, [update_row(datetime(2026, 10, 2, 8, 0), "b", 120, scheduled_time="08:00:00")])
    ch_aggregates.build(ch_client, "agg_hour_daily", AGENCY, [OCT])
    with pytest.raises(RuntimeError, match="outside the rebuild"):
        ch_aggregates.swap(ch_client, "agg_hour_daily", AGENCY, [SEPT])
    assert _hour_daily(ch_client, "agg_hour_daily") == []


def test_a_full_rebuild_covers_every_month_in_updates_or_in_the_table(ch_client):
    insert_updates(ch_client, AGENCY, [update_row(datetime(2026, 8, 3, 8, 0), "a", 60, scheduled_time="08:00:00")])
    _rebuild(ch_client, AGENCY, [AUG])
    ch_client.command("TRUNCATE TABLE updates")
    insert_updates(ch_client, AGENCY, [update_row(datetime(2026, 10, 2, 8, 0), "b", 60, scheduled_time=None)])
    assert ch_aggregates.months_for(ch_client, "agg_hour_daily", AGENCY, None) == [AUG, OCT]
    assert ch_aggregates.months_for(ch_client, "agg_hour_daily", AGENCY, [SEPT]) == [SEPT]


def test_totals_and_rows_cover_only_the_requested_months_of_the_agency(ch_client):
    insert_updates(
        ch_client,
        AGENCY,
        [
            update_row(datetime(2026, 9, 14, 8, 0), "a", 60, scheduled_time="08:00:00"),
            update_row(datetime(2026, 10, 2, 8, 0), "b", 120, scheduled_time="08:00:00"),
        ],
    )
    insert_updates(ch_client, OTHER, [update_row(datetime(2026, 9, 14, 8, 0), "x", 30, scheduled_time="08:00:00")])
    for agency_id in (AGENCY, OTHER):
        _rebuild(ch_client, agency_id, ch_aggregates.months_for(ch_client, "agg_hour_daily", agency_id, None))
    assert ch_aggregates.totals(ch_client, "agg_hour_daily", AGENCY, None) == (2, 2, 180)
    assert ch_aggregates.totals(ch_client, "agg_hour_daily", AGENCY, [OCT]) == (1, 1, 120)
    sql, params = ch_aggregates.rows_sql("agg_hour_daily", AGENCY, [SEPT])
    assert ch_client.query(sql, parameters=params).result_rows == [
        (AGENCY, date(2026, 9, 14), 8, Decimal("1.00"), 1, 60)
    ]


def test_dropping_an_agencys_work_tables_leaves_another_agencys_alone(ch_client):
    for agency_id in (AGENCY, OTHER):
        ch_aggregates.build(ch_client, "agg_hour_daily", agency_id, [])
    ch_aggregates.drop_work_tables(ch_client, AGENCY)
    assert work_tables(ch_client) == [ch_aggregates.work_table("agg_hour_daily", OTHER)]


def test_a_bootstrapped_clickhouse_lacks_no_aggregate_table(ch_client):
    assert ch_aggregates.missing_tables(ch_client) == []
