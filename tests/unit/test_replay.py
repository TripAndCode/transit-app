"""Pure-logic coverage for ``GET /api/agencies/{agency_id}/replay``.

The endpoint itself needs ClickHouse and Postgres, but window validation,
the rendered SQL, and the row -> columnar-response transform are factored
into pure helpers so they can be pinned without a database.
"""

import pytest
from fastapi import HTTPException

from api.routers.replay import (
    MAX_REPLAY_WINDOW_HOURS,
    build_default_window_probe_sql,
    build_replay_sql,
    build_replay_trips,
    validate_replay_window,
)
from pipeline.service_day import filed_between_ch
from pipeline.static_archive import Stamp

# --- window validation -------------------------------------------------


def test_window_accepts_a_three_hour_span():
    validate_replay_window(4, 7)


def test_window_accepts_both_absent():
    validate_replay_window(None, None)


def test_window_rejects_from_hour_alone():
    with pytest.raises(HTTPException) as exc:
        validate_replay_window(4, None)
    assert exc.value.status_code == 422


def test_window_rejects_to_hour_alone():
    with pytest.raises(HTTPException) as exc:
        validate_replay_window(None, 7)
    assert exc.value.status_code == 422


def test_window_rejects_a_backwards_or_empty_span():
    with pytest.raises(HTTPException) as exc:
        validate_replay_window(7, 7)
    assert exc.value.status_code == 422
    with pytest.raises(HTTPException) as exc:
        validate_replay_window(7, 4)
    assert exc.value.status_code == 422


def test_window_rejects_a_span_wider_than_the_cap():
    with pytest.raises(HTTPException) as exc:
        validate_replay_window(0, MAX_REPLAY_WINDOW_HOURS + 1)
    assert exc.value.status_code == 422


def test_window_accepts_exactly_the_cap():
    validate_replay_window(0, MAX_REPLAY_WINDOW_HOURS)


# --- rendered SQL --------------------------------------------------------


def test_replay_sql_filters_the_target_date_in_jst():
    sql = build_replay_sql(Stamp.INGESTED, "updates")
    assert filed_between_ch("{target_date:Date}", "{target_date:Date}") in sql
    assert "toDate(u.captured_at)" not in sql


def test_replay_sql_reads_every_column_off_one_winning_row():
    sql = build_replay_sql(Stamp.INGESTED, "updates")
    # One tuple-argMax per stop event -- a captured_at tie resolved per
    # column independently could mix a delay from one physical row with a
    # route_code from another.
    assert sql.count("argMax(") == 1
    columns = ("dep_delay", "stop_id", "route_code", "scheduled_sec", "scheduled_time", "canceled")
    for i, name in enumerate(columns, start=1):
        assert f"winner.{i} AS {name}" in sql


def test_replay_sql_keeps_a_withdrawn_stop_events_winning_row():
    sql = build_replay_sql(Stamp.INGESTED, "updates")
    # Unlike every other dedup reader, a withdrawn stop event's winning row
    # (dep_delay NULL) must survive so the trip's last delay can carry
    # forward -- so this query must not filter the winner on dep_delay.
    assert "HAVING" not in sql


def test_replay_sql_orders_by_trip_then_stop_sequence():
    sql = build_replay_sql(Stamp.INGESTED, "updates")
    assert "ORDER BY trip_id, stop_sequence" in sql


def test_replay_sql_tests_the_hour_window_on_the_filed_schedule():
    sql = build_replay_sql(Stamp.INGESTED, "updates")
    assert "{from_sec:UInt32}" in sql
    assert "{to_sec:UInt32}" in sql


def test_default_window_probe_shares_the_survivor_definition():
    sql = build_default_window_probe_sql(Stamp.INGESTED, "updates")
    assert "HAVING tupleElement(winner, 1) IS NOT NULL" in sql
    assert "min(tupleElement(winner, 2))" in sql


def test_replay_sql_under_refit_joins_the_archive_and_keeps_its_placeholders():
    sql = build_replay_sql(Stamp.REFIT, "updates")
    assert "ANY LEFT JOIN" in sql
    assert "{target_date:Date}" in sql
    assert "{from_sec:UInt32}" in sql
    assert "{to_sec:UInt32}" in sql
    assert "ORDER BY trip_id, stop_sequence" in sql


def test_default_window_probe_under_refit_joins_the_archive_and_keeps_its_placeholders():
    sql = build_default_window_probe_sql(Stamp.REFIT, "updates")
    assert "ANY LEFT JOIN" in sql
    assert "{target_date:Date}" in sql
    assert "HAVING tupleElement(winner, 1) IS NOT NULL" in sql


# --- row -> columnar transform -------------------------------------------


def _row(trip_id, stop_sequence, dep_delay, stop_id, route_code, scheduled_sec, scheduled_time=None, canceled=False):
    return (trip_id, stop_sequence, dep_delay, stop_id, route_code, scheduled_sec, scheduled_time, canceled)


def test_build_replay_trips_packs_columnar_fields():
    rows = [
        _row("t1", 1, 30, "s1", "R1", 4 * 3600),
        _row("t1", 2, 60, "s2", "R1", 4 * 3600 + 600),
    ]
    trips, stops, routes = build_replay_trips(rows)
    assert stops == ["s1", "s2"]
    assert routes == ["R1"]
    assert len(trips) == 1
    trip = trips[0]
    assert trip.trip_id == "t1"
    assert trip.route_code == "R1"
    assert trip.stop_idx == [0, 1]
    assert trip.t == [4 * 3600 + 30, 4 * 3600 + 600 + 60]
    assert trip.delay == [30, 60]


def test_build_replay_trips_orders_by_first_observed_second():
    rows = [
        _row("later", 1, 0, "s1", "R1", 10 * 3600),
        _row("earlier", 1, 0, "s2", "R1", 4 * 3600),
    ]
    trips, _stops, _routes = build_replay_trips(rows)
    assert [t.trip_id for t in trips] == ["earlier", "later"]


def test_build_replay_trips_drops_a_stop_with_no_usable_scheduled_time():
    rows = [
        _row("t1", 1, 30, "s1", "R1", 4 * 3600),
        _row("t1", 2, 60, "s2", "R1", None, None),
    ]
    trips, stops, _routes = build_replay_trips(rows)
    assert stops == ["s1"]
    assert trips[0].stop_idx == [0]


def test_build_replay_trips_omits_a_null_route_code_from_the_route_list():
    rows = [_row("t1", 1, 30, "s1", None, 4 * 3600)]
    trips, _stops, routes = build_replay_trips(rows)
    assert routes == []
    assert trips[0].route_code is None


def test_build_replay_trips_falls_back_to_the_clock_string_without_scheduled_sec():
    rows = [_row("t1", 1, 0, "s1", "R1", None, "04:10:00")]
    trips, _stops, _routes = build_replay_trips(rows)
    assert trips[0].t == [4 * 3600 + 600]


def test_build_replay_trips_carries_a_withdrawn_stops_delay_forward():
    rows = [
        _row("t1", 1, 30, "s1", "R1", 4 * 3600),
        _row("t1", 2, None, "s2", "R1", 4 * 3600 + 600),
        _row("t1", 3, 90, "s3", "R1", 4 * 3600 + 1200),
    ]
    trips, stops, _routes = build_replay_trips(rows)
    assert stops == ["s1", "s2", "s3"]
    trip = trips[0]
    assert trip.stop_idx == [0, 1, 2]
    assert trip.delay == [30, 30, 90]
    assert trip.t == [4 * 3600 + 30, 4 * 3600 + 600 + 30, 4 * 3600 + 1200 + 90]


def test_build_replay_trips_omits_a_withdrawn_stop_with_no_prior_delay():
    rows = [
        _row("t1", 1, None, "s1", "R1", 4 * 3600),
        _row("t1", 2, 60, "s2", "R1", 4 * 3600 + 600),
    ]
    trips, stops, _routes = build_replay_trips(rows)
    assert stops == ["s2"]
    assert trips[0].stop_idx == [0]
    assert trips[0].t == [4 * 3600 + 600 + 60]


def test_build_replay_trips_omits_a_canceled_stop_rather_than_carrying_onto_it():
    rows = [
        _row("t1", 1, 30, "s1", "R1", 4 * 3600),
        _row("t1", 2, None, "s2", "R1", 4 * 3600 + 600, canceled=True),
    ]
    trips, stops, _routes = build_replay_trips(rows)
    assert stops == ["s1"]
    assert trips[0].stop_idx == [0]
    assert trips[0].delay == [30]


def test_build_replay_trips_drops_a_trip_whose_every_row_is_withdrawn():
    rows = [
        _row("t1", 1, None, "s1", "R1", 4 * 3600),
        _row("t1", 2, None, "s2", "R1", 4 * 3600 + 600),
    ]
    trips, stops, routes = build_replay_trips(rows)
    assert trips == []
    assert stops == []
    assert routes == []


def test_build_replay_trips_drops_a_trip_canceled_before_its_first_kept_stop():
    rows = [_row("t1", 1, None, "s1", "R1", 4 * 3600, canceled=True)]
    trips, stops, routes = build_replay_trips(rows)
    assert trips == []
    assert stops == []
    assert routes == []
