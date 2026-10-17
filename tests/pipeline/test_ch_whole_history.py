"""The whole-history aggregates' ClickHouse side: the delay counts and their finalization."""

from datetime import date, datetime

from pipeline import ch_aggregates
from pipeline.clickhouse import insert_updates
from tests.pipeline.ch_agg_helpers import update_row, whole_history_rows

AGENCY = 7101
OTHER = 7102
SEPT = 202609
OCT = 202610
COUNTS = ch_aggregates.COUNTS


def _null_last(row: tuple) -> tuple:
    return tuple((value is None, value) for value in row)


def _counts(ch_client, table: str = COUNTS, agency_id: int = AGENCY) -> list[tuple]:
    return sorted(
        ch_client.query(
            f"SELECT date, route_code, service_type, scheduled_sec, dep_delay, samples, sum_delay_sec FROM {table} "
            "WHERE agency_id = {agency_id:Int32}",
            parameters={"agency_id": agency_id},
        ).result_rows,
        key=_null_last,
    )


def _recount(ch_client, months: list[int], changed=None, agency_id: int = AGENCY) -> None:
    ch_aggregates.build(ch_client, agency_id, {COUNTS: ch_aggregates.Rebuild(months, changed)})
    ch_aggregates.swap(ch_client, COUNTS, agency_id, months)


def test_counts_hold_each_typed_routed_value_once_per_day_and_scheduled_second(ch_client):
    insert_updates(ch_client, AGENCY, whole_history_rows())
    _recount(ch_client, [SEPT, OCT])
    sun, mon, fri = date(2026, 9, 13), date(2026, 9, 14), date(2026, 10, 2)
    at_0705 = 7 * 3600 + 5 * 60
    assert _counts(ch_client) == sorted(
        [
            (sun, "WH", "平日", at_0705, 60, 1, 60),
            (sun, "WH", "平日", at_0705, 90, 1, 90),
            (sun, "WH", "平日", 15 * 3600, -30, 1, -30),
            (mon, "WH", "平日", at_0705, 60, 1, 60),
            (mon, "WH", "平日", at_0705, 61, 1, 61),
            (mon, "WH", "平日", None, 120, 1, 120),
            (fri, "WH", "平日", at_0705, 1, 1, 1),
            (fri, "WH", "平日", at_0705, 2, 1, 2),
        ],
        key=_null_last,
    )


def test_counts_merge_equal_values_into_one_row(ch_client):
    day = datetime(2026, 9, 14, 8, 0)
    insert_updates(ch_client, AGENCY, [update_row(day, f"t{i}", 30, route_code="SAME") for i in range(3)])
    _recount(ch_client, [SEPT])
    assert _counts(ch_client) == [(date(2026, 9, 14), "SAME", "平日", 8 * 3600 + 10 * 60, 30, 3, 90)]


HOUR_DOW = "agg_route_hour_dow"


def _hour_dow(ch_client, table: str = HOUR_DOW, agency_id: int = AGENCY) -> list[tuple]:
    rows = ch_client.query(
        f"SELECT route_code, service_type, dow, hour, avg_min, samples, sum_delay_sec FROM {table} "
        "WHERE agency_id = {agency_id:Int32}",
        parameters={"agency_id": agency_id},
    ).result_rows
    return sorted((r[0], r[1], r[2], r[3], round(float(r[4]), 2), r[5], r[6]) for r in rows)


def _finalize(ch_client, counts_months=None, agency_id: int = AGENCY) -> None:
    ch_aggregates.build_whole_history(ch_client, agency_id, [HOUR_DOW], counts_months)
    ch_aggregates.swap_whole_history(ch_client, HOUR_DOW, agency_id)


def test_route_hour_dow_numbers_days_iso_monday_one_sunday_seven(ch_client):
    insert_updates(ch_client, AGENCY, whole_history_rows())
    _recount(ch_client, [SEPT, OCT])
    _finalize(ch_client)
    assert _hour_dow(ch_client) == [
        ("WH", "平日", 1, 7, 1.01, 2, 121),  # Monday 09-14: 60 and 61
        ("WH", "平日", 5, 7, 0.03, 2, 3),  # Friday 10-02: 1 and 2, mean 1.5 s rounds up to 0.03
        ("WH", "平日", 7, 7, 1.25, 2, 150),  # Sunday 09-13: 60 and 90
        ("WH", "平日", 7, 15, -0.5, 1, -30),
    ]


def test_finalizing_reads_rebuilt_months_from_the_work_table_and_the_rest_from_the_counts(ch_client):
    insert_updates(ch_client, AGENCY, whole_history_rows())
    _recount(ch_client, [SEPT, OCT])
    ch_client.command("TRUNCATE TABLE updates")
    insert_updates(ch_client, AGENCY, [r for r in whole_history_rows() if r[1].month == 9])
    ch_aggregates.build(ch_client, AGENCY, {COUNTS: ch_aggregates.Rebuild([OCT], None)})
    _finalize(ch_client, counts_months=[OCT])
    assert [row[2] for row in _hour_dow(ch_client)] == [1, 7, 7], "October's counts came from the emptied work table"


def test_swapping_replaces_only_this_agencys_partition_and_drops_an_empty_one(ch_client):
    insert_updates(ch_client, OTHER, whole_history_rows())
    _recount(ch_client, [SEPT, OCT], agency_id=OTHER)
    _finalize(ch_client, agency_id=OTHER)
    insert_updates(ch_client, AGENCY, whole_history_rows())
    _recount(ch_client, [SEPT, OCT])
    _finalize(ch_client)
    assert len(_hour_dow(ch_client)) == 4

    ch_client.command("TRUNCATE TABLE updates")
    _recount(ch_client, [SEPT, OCT])
    _finalize(ch_client)
    assert _hour_dow(ch_client) == []
    assert len(_hour_dow(ch_client, agency_id=OTHER)) == 4


def test_the_read_only_path_computes_the_counts_in_query(ch_client):
    insert_updates(ch_client, AGENCY, whole_history_rows())
    _recount(ch_client, [SEPT, OCT])
    _finalize(ch_client)
    rows = ch_client.query(ch_aggregates.whole_history_sql(HOUR_DOW), parameters={"agency_id": AGENCY}).result_rows
    in_query = sorted((r[1], r[2], r[3], r[4], round(float(r[5]), 2), r[6], r[7]) for r in rows)
    assert in_query == _hour_dow(ch_client)
