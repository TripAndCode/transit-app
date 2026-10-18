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
    recounted = {ch_aggregates.COUNTS: counts_months} if counts_months else {}
    ch_aggregates.build_whole_history(ch_client, agency_id, [HOUR_DOW], recounted)
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


def _stats(ch_client, agency_id: int = AGENCY) -> dict:
    rows = ch_client.query(
        "SELECT route_code, avg_min, p50_min, p90_min, late_5min_plus, on_time_pct, late5_pct, samples "
        "FROM agg_route_stats WHERE agency_id = {agency_id:Int32}",
        parameters={"agency_id": agency_id},
    ).result_rows
    return {
        r[0]: (
            round(float(r[1]), 2),
            round(float(r[2]), 2),
            round(float(r[3]), 2),
            r[4],
            round(float(r[5]), 1),
            round(float(r[6]), 1),
            r[7],
        )
        for r in rows
    }


def _values(route: str, delays: list[int]) -> list[tuple]:
    day = datetime(2026, 9, 14, 8, 0)
    return [update_row(day, f"{route}{i}", d, route_code=route) for i, d in enumerate(delays)]


def _finalize_table(ch_client, table: str, agency_id: int = AGENCY) -> None:
    ch_aggregates.build_whole_history(ch_client, agency_id, [table], {})
    ch_aggregates.swap_whole_history(ch_client, table, agency_id)


def test_percentiles_take_the_value_postgres_percentile_disc_takes(ch_client):
    insert_updates(
        ch_client,
        AGENCY,
        [
            *_values("ONE", [42]),
            *_values("TWO", [10, 20]),
            *_values("TEN", list(range(1, 11))),
            *_values("TIE", [1, 2, 2, 2, 3]),
            *_values("NEG", [-90, -30, 0, 30]),
        ],
    )
    _recount(ch_client, [SEPT])
    _finalize_table(ch_client, "agg_route_stats")
    got = {route: (p50, p90) for route, (_, p50, p90, *_rest) in _stats(ch_client).items()}
    assert got == {
        "ONE": (0.7, 0.7),
        "TWO": (0.17, 0.33),  # positions ceil(1.0) = 1 and ceil(1.8) = 2
        "TEN": (0.08, 0.15),  # 5th and 9th values, 5 s and 9 s
        "TIE": (0.03, 0.05),  # 3rd value 2 s; 5th value 3 s
        "NEG": (-0.5, 0.5),  # 2nd value -30 s; ceil(3.6) = 4th value 30 s
    }


def test_route_stats_rounds_percentages_half_away_from_zero(ch_client):
    # One on time in sixteen is 6.25 %, which rounds to 6.3 (half to even would give 6.2).
    insert_updates(ch_client, AGENCY, _values("PCT", [0] + [400] * 15))
    _recount(ch_client, [SEPT])
    _finalize_table(ch_client, "agg_route_stats")
    avg, _, _, late5, on_time_pct, late5_pct, samples = _stats(ch_client)["PCT"]
    assert (late5, on_time_pct, late5_pct, samples) == (15, 6.3, 93.8, 16)
    assert avg == 6.25  # 6000 s / 16 / 60 = 6.25 exactly


def test_route_hour_merges_a_time_written_with_and_without_seconds(ch_client):
    insert_updates(ch_client, AGENCY, whole_history_rows())
    _recount(ch_client, [SEPT, OCT])
    _finalize_table(ch_client, "agg_route_hour")
    rows = ch_client.query(
        "SELECT scheduled_time, samples FROM agg_route_hour "
        "WHERE agency_id = {agency_id:Int32} ORDER BY scheduled_time",
        parameters={"agency_id": AGENCY},
    ).result_rows
    assert rows == [("07:05:00", 6), ("15:00:00", 1)]


def test_a_routes_distribution_holds_each_delay_value_once_whatever_its_scheduled_times(ch_client):
    """agg_route_stats sorts one array per route; a value seen at two departure
    times must be one entry, or the array grows with the timetable, not the delays."""
    day = datetime(2026, 9, 14, 8, 0)
    insert_updates(
        ch_client,
        AGENCY,
        [
            update_row(day, "a", 30, scheduled_time="07:00:00", route_code="ONE"),
            update_row(day, "b", 30, scheduled_time="08:00:00", route_code="ONE"),
            update_row(day, "c", 45, scheduled_time="08:00:00", route_code="ONE"),
        ],
    )
    _recount(ch_client, [SEPT])
    source = f"SELECT * FROM {COUNTS} WHERE agency_id = {{agency_id:Int32}}"
    sql = ch_aggregates._distribution(source, "route_code, service_type")
    ((dist,),) = ch_client.query(f"SELECT dist FROM ({sql})", parameters={"agency_id": AGENCY}).result_rows
    assert [tuple(pair) for pair in dist] == [(30, 2), (45, 1)]
