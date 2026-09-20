"""Pure-logic coverage for ``/today/route/{route_code}/trips``.

The endpoint itself needs ClickHouse and Postgres, but everything that decides
*what* it asks for and *what shape* it answers with is factored into pure
helpers so it can be pinned without a database: the rendered ClickHouse SQL
text, the requested-date window, the clock-string fallback, and the row ->
per-trip-with-stops transform that backs the Marey diagram.
"""

from datetime import date, timedelta

import pytest

from api.routers.map import (
    MAX_ROUTE_TRIPS,
    ROUTE_TRIPS_DATE_WINDOW_DAYS,
    build_route_trips,
    build_route_trips_sql,
    clock_to_sec,
    resolve_route_trips_date,
)

# --- rendered SQL -----------------------------------------------------------


def test_route_trips_sql_filters_the_target_date_in_jst():
    sql, params = build_route_trips_sql("all")
    assert "toDate(u.captured_at, 'Asia/Tokyo') = {target_date:Date}" in sql
    # A bare toDate() would bucket a JST evening observation into the previous
    # UTC day, which is the same class of bug analyze's connection timezone fix
    # addressed.
    assert "toDate(u.captured_at)" not in sql
    assert params == {}


def test_route_trips_sql_reads_every_marey_column_off_one_winning_row():
    sql, _ = build_route_trips_sql("all")
    # One tuple-argMax, not four per-column argMax calls: a captured_at tie
    # resolved independently per column could mix a scheduled time from one
    # physical row with a delay from another.
    assert sql.count("argMax(") == 1
    assert "argMax(tuple(u.scheduled_time, u.dep_delay, u.stop_id, u.scheduled_sec)" in sql
    for i, name in enumerate(("scheduled_time", "dep_delay", "stop_id", "scheduled_sec"), start=1):
        assert f"winner.{i} AS {name}" in sql


def test_route_trips_sql_orders_by_trip_then_stop_sequence():
    sql, _ = build_route_trips_sql("all")
    # A polyline is only meaningful if its points arrive in stop order, and a
    # bare GROUP BY has no defined output order.
    assert "ORDER BY trip_id, stop_sequence" in sql


def test_route_trips_sql_all_band_adds_no_time_filter():
    sql, params = build_route_trips_sql("all")
    assert "scheduled_time, 1, 5" not in sql
    assert params == {}


def test_route_trips_sql_named_band_filters_on_the_five_char_clock_prefix():
    sql, params = build_route_trips_sql("morning")
    assert "substring(scheduled_time, 1, 5) >= {ch_tb_start:String}" in sql
    assert "substring(scheduled_time, 1, 5) < {ch_tb_end:String}" in sql
    assert params == {"ch_tb_start": "05:00", "ch_tb_end": "09:00"}


def test_route_trips_sql_keeps_the_agency_and_route_bounds_in_every_band():
    for band in ("all", "morning", "late_night"):
        sql, _ = build_route_trips_sql(band)
        assert "u.agency_id = {agency_id:UInt16}" in sql
        assert "u.route_code = {route:String}" in sql
        assert "u.dep_delay IS NOT NULL" in sql


# --- requested date ---------------------------------------------------------


def test_resolve_route_trips_date_defaults_to_the_latest_observed_day():
    assert resolve_route_trips_date(None, date(2026, 9, 18)) == date(2026, 9, 18)


def test_resolve_route_trips_date_accepts_a_day_inside_the_window():
    latest = date(2026, 9, 18)
    assert resolve_route_trips_date(date(2026, 9, 11), latest) == date(2026, 9, 11)
    edge = latest - timedelta(days=ROUTE_TRIPS_DATE_WINDOW_DAYS)
    assert resolve_route_trips_date(edge, latest) == edge


def test_resolve_route_trips_date_rejects_a_day_outside_the_scan_window():
    latest = date(2026, 9, 18)
    # Older than the probe's own 30-day bound: answering would mean an
    # unbounded historical scan of a hundreds-of-millions-of-rows table.
    assert resolve_route_trips_date(date(2026, 1, 1), latest) is None
    # Later than anything observed: there is nothing to draw.
    assert resolve_route_trips_date(date(2026, 9, 19), latest) is None


# --- clock fallback ---------------------------------------------------------


def test_clock_to_sec_reads_both_ingest_strategies_clock_shapes():
    assert clock_to_sec("08:40") == 8 * 3600 + 40 * 60
    assert clock_to_sec("08:40:30") == 8 * 3600 + 40 * 60 + 30


def test_clock_to_sec_returns_none_for_anything_it_cannot_place():
    for bad in (None, "", "eight", "8", "08:6x"):
        assert clock_to_sec(bad) is None


def test_clock_to_sec_accepts_a_post_midnight_continuation_hour():
    assert clock_to_sec("25:30") == 25 * 3600 + 30 * 60


# --- row -> trip transform --------------------------------------------------


def _row(trip, seq, clock, delay, stop_id="S", sched_sec=None):
    return (trip, seq, clock, delay, stop_id, sched_sec)


def test_build_route_trips_groups_stops_under_their_trip():
    trips, truncated = build_route_trips(
        [
            _row("A", 1, "08:40", 600, "S1", 31200),
            _row("A", 2, "08:50", 540, "S2", 31800),
            _row("B", 1, "12:05", 120, "S1", 43500),
        ],
        {"A": "造道行"},
    )
    assert truncated is False
    by_id = {t.trip_id: t for t in trips}
    assert [s.stop_sequence for s in by_id["A"].stops] == [1, 2]
    assert [s.stop_id for s in by_id["A"].stops] == ["S1", "S2"]
    assert by_id["A"].headsign == "造道行"
    assert by_id["B"].headsign is None
    assert by_id["B"].samples == 1


def test_build_route_trips_reports_observed_as_scheduled_plus_delay():
    trips, _ = build_route_trips([_row("A", 1, "08:40", 600, "S1", 31200)], {})
    stop = trips[0].stops[0]
    assert stop.scheduled_sec == 31200
    assert stop.delay_sec == 600
    assert stop.observed_sec == 31800


def test_build_route_trips_falls_back_to_the_clock_string_when_scheduled_sec_is_null():
    # Agency 1's ingest strategy writes a "HH:MM" clock and no scheduled_sec.
    trips, _ = build_route_trips([_row("A", 1, "08:40", 600, "S1", None)], {})
    assert trips[0].stops[0].scheduled_sec == 31200


def test_build_route_trips_leaves_an_unplaceable_stop_without_a_time():
    trips, _ = build_route_trips([_row("A", 1, None, 600, "S1", None)], {})
    stop = trips[0].stops[0]
    assert stop.scheduled_sec is None
    assert stop.observed_sec is None
    assert stop.delay_sec == 600


def test_build_route_trips_keeps_the_worst_first_ordering():
    trips, _ = build_route_trips(
        [_row("A", 1, "08:00", 60), _row("B", 1, "09:00", 900), _row("C", 1, "10:00", 300)],
        {},
    )
    assert [t.trip_id for t in trips] == ["B", "C", "A"]


def test_build_route_trips_summarises_each_trip_the_way_the_drilldown_list_does():
    trips, _ = build_route_trips([_row("A", 1, "08:40", 600), _row("A", 2, "08:50", 540)], {})
    assert trips[0].avg_delay_sec == 570
    assert trips[0].scheduled_time == "08:40"
    assert trips[0].samples == 2


def test_build_route_trips_bounds_the_payload_and_says_so():
    rows = [_row(f"T{i:04d}", 1, "08:00", i) for i in range(MAX_ROUTE_TRIPS + 5)]
    trips, truncated = build_route_trips(rows, {})
    assert len(trips) == MAX_ROUTE_TRIPS
    assert truncated is True
    # The cap keeps the worst trips, which is the ordering the list already
    # promises; dropping the least-delayed tail loses the least.
    assert trips[0].avg_delay_sec == MAX_ROUTE_TRIPS + 4


@pytest.mark.parametrize("limit", [1, 3])
def test_build_route_trips_reports_no_truncation_when_it_fits(limit):
    rows = [_row(f"T{i}", 1, "08:00", i) for i in range(limit)]
    _, truncated = build_route_trips(rows, {}, limit=limit)
    assert truncated is False
