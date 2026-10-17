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
