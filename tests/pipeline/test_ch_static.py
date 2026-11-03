"""The static-joined aggregates' ClickHouse side: the stop tables, joined to the timetable archive."""

from datetime import date, datetime

from pipeline import ch_aggregates, static_archive
from pipeline.clickhouse import insert_updates
from tests.pipeline.ch_agg_helpers import update_row

AGENCY = 7201
SEPT = 202609
# The stop tables' rows carry no version name here, so each joins the loaded timetable, KEY.
KEY = "c" * 32
JOIN = static_archive.ScheduleJoin([], [], KEY)


def _archive(ch_client, rows: list[tuple[str, int, str]]) -> None:
    """(trip_id, stop_sequence, stop_id) rows archived for AGENCY as the loaded timetable."""
    ch_client.insert(
        static_archive.ARCHIVE,
        [(AGENCY, KEY, trip, sequence, stop, None, None) for trip, sequence, stop in rows],
        column_names=[
            "agency_id",
            "content_hash",
            "trip_id",
            "stop_sequence",
            "stop_id",
            "arrival_time",
            "departure_time",
        ],
    )


def _stop_daily(ch_client, table: str = "agg_stop_daily") -> list[tuple]:
    columns = ", ".join(c for c in ch_aggregates.COLUMNS[table] if c != "agency_id")
    return sorted(
        ch_client.query(
            f"SELECT {columns} FROM {table} WHERE agency_id = {{a:Int32}}", parameters={"a": AGENCY}
        ).result_rows
    )


def _build(ch_client, tables: list[str]) -> None:
    ch_aggregates.build(ch_client, AGENCY, {t: ch_aggregates.Rebuild([SEPT], None) for t in tables}, join=JOIN)
    for table in tables:
        ch_aggregates.swap(ch_client, table, AGENCY, [SEPT])


def test_stop_daily_bands_scheduled_times_like_postgres(ch_client):
    day = datetime(2026, 9, 14, 8, 0)
    _archive(ch_client, [(f"t{i}", 1, "S1") for i in range(7)])
    times = ["04:59:59", "05:00:00", "8:59", "23:59:59", "0:05", None, "12:00"]
    insert_updates(ch_client, AGENCY, [update_row(day, f"t{i}", 60, scheduled_time=t) for i, t in enumerate(times)])
    _build(ch_client, ["agg_stop_daily"])
    bands = {row[3]: row[5] for row in _stop_daily(ch_client)}
    assert bands == {"late_night": 2, "morning": 2, "night": 1, "none": 1, "noon": 1}


def test_stop_daily_joins_on_trip_and_sequence_only(ch_client):
    day = datetime(2026, 9, 14, 8, 0)
    _archive(ch_client, [("t1", 40000, "BIG"), ("t1", 1, "S1")])
    insert_updates(
        ch_client,
        AGENCY,
        [
            update_row(day, "t1", 30, stop_sequence=40000),
            update_row(day, "t1", 90, stop_sequence=1, service_type=None),
            update_row(day, "t1", 999, stop_sequence=2),  # no such stop in the schedule
            update_row(day, "t9", 999, stop_sequence=1),  # no such trip
        ],
    )
    _build(ch_client, ["agg_stop_daily", "agg_route_stop_daily"])
    assert _stop_daily(ch_client) == [
        ("BIG", date(2026, 9, 14), "平日", "morning", 30, 1),
        ("S1", date(2026, 9, 14), "", "morning", 90, 1),
    ]
    assert [row[:2] for row in _stop_daily(ch_client, "agg_route_stop_daily")] == [("R1", "BIG"), ("R1", "S1")]


def _stop_routes(ch_client) -> dict[str, str]:
    rows = ch_client.query(
        "SELECT stop_id, route_codes FROM agg_stop_routes WHERE agency_id = {a:Int32}", parameters={"a": AGENCY}
    ).result_rows
    return dict(rows)


def _build_stop_routes(ch_client) -> None:
    """S1 seen on a route with no delay, on one with a delay, and on no route at all."""
    day = datetime(2026, 9, 14, 8, 0)
    _archive(ch_client, [("t1", 1, "S1"), ("t2", 1, "S1")])
    insert_updates(
        ch_client,
        AGENCY,
        [
            update_row(day, "t1", None, route_code="QUIET"),
            update_row(day, "t2", 30, route_code="BUSY"),
            update_row(day, "t2", 30, route_code=None),
        ],
    )
    ch_aggregates.build(
        ch_client, AGENCY, {ch_aggregates.ROUTE_STOP_DAYS: ch_aggregates.Rebuild([SEPT], None)}, join=JOIN
    )
    ch_aggregates.swap(ch_client, ch_aggregates.ROUTE_STOP_DAYS, AGENCY, [SEPT])
    ch_aggregates.build_whole_history(ch_client, AGENCY, ["agg_stop_routes"], {})
    ch_aggregates.swap_whole_history(ch_client, "agg_stop_routes", AGENCY)


def test_stop_routes_keep_a_route_whose_every_observation_lacks_a_delay(ch_client):
    _build_stop_routes(ch_client)
    assert _stop_routes(ch_client) == {"S1": "BUSY,QUIET"}


def test_the_read_only_path_computes_the_stop_routes_in_query(ch_client):
    _build_stop_routes(ch_client)
    sql = ch_aggregates.whole_history_sql("agg_stop_routes")
    rows = ch_client.query(sql, parameters={"agency_id": AGENCY, **JOIN.params()}).result_rows
    assert {stop_id: codes for _, stop_id, codes in rows} == _stop_routes(ch_client)
