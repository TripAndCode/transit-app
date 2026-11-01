"""Proves build_stop_visits_ch_sql pairs each stop visit's arrival with its
departure when the feed reports them in separate polls, which is the only way
the static_join feeds send them: a StopTimeUpdate row carries an arrival or a
departure, never both. Each trip's last stop gets arrival polls only.

Also proves it tells an observed departure (its poll at or after the visit's
first arrival poll) from a predicted one, and that its departure side is the
shared dedup's: for every visit with a departure, the delay, the schedule and
the row count are exactly what build_dedup_ch_sql resolves."""

import os
from datetime import date, datetime, timedelta, timezone

import clickhouse_connect
import pytest

from pipeline.db import MAX_PLAUSIBLE_DELAY_SEC, build_dedup_ch_sql, build_stop_visits_ch_sql

pytestmark = pytest.mark.skipif(os.environ.get("RUN_CH_INTEGRATION") != "1", reason="requires `make ch-test`")

# 11:00 JST on 1 April, well inside the 1 April service day.
T0 = datetime(2026, 4, 1, 2, 0, 0, tzinfo=timezone.utc)


def _at(seconds: int) -> datetime:
    return T0 + timedelta(seconds=seconds)


def _poll(
    file_name,
    captured_at,
    stop_sequence,
    *,
    dep=None,
    arr=None,
    trip_id="T1",
    route_code="R1",
    service_type="weekday",
    scheduled_time="10:00",
    scheduled_sec=36000,
):
    """One StopTimeUpdate row of one poll, shaped for insert_updates."""
    return (
        file_name,
        captured_at,
        trip_id,
        service_type,
        scheduled_time,
        route_code,
        stop_sequence,
        dep,
        "S1",
        arr,
        None,
        None,
        None,
        scheduled_sec,
        None,
    )


def _client():
    return clickhouse_connect.get_client(
        host="localhost",
        port=int(os.environ.get("CLICKHOUSE_TEST_PORT", "8124")),
        username="transit",
        password="transit",
        database="transit_test",
    )


def _query(polls, sql, parameters=None):
    """Load *polls* into a fresh `updates` and return *sql*'s rows as dicts."""
    from db.clickhouse.bootstrap import apply_schema
    from pipeline.clickhouse import insert_updates

    client = _client()
    try:
        client.command("DROP TABLE IF EXISTS updates")
        apply_schema(client)
        insert_updates(client, 1, polls)
        result = client.query(sql, parameters={"agency_id": 1, **(parameters or {})})
        return [dict(zip(result.column_names, row, strict=True)) for row in result.result_rows]
    finally:
        client.close()


def _visits(polls, **kwargs):
    rows = _query(polls, build_stop_visits_ch_sql(**kwargs))
    return sorted(rows, key=lambda r: (r["trip_id"], r["date"], r["stop_sequence"]))


def _delays(polls):
    return [(r["stop_sequence"], r["dep_delay"], r["arr_delay"]) for r in _visits(polls)]


def test_stop_visits_pair_an_arrival_and_a_departure_from_separate_polls():
    polls = [
        _poll("p1.pb", _at(0), 2, dep=50),
        _poll("p2.pb", _at(60), 2, arr=20),
    ]
    assert _delays(polls) == [(2, 50, 20)]


def test_stop_visits_keep_the_arrival_when_a_later_poll_carries_only_the_departure():
    """A later departure poll says nothing about the arrival: the feed never
    puts both on one row, so its NULL arrival is not a retraction."""
    polls = [
        _poll("p1.pb", _at(0), 2, arr=20),
        _poll("p2.pb", _at(60), 2, dep=50),
    ]
    assert _delays(polls) == [(2, 50, 20)]


def test_stop_visits_take_the_latest_of_several_arrival_polls():
    polls = [
        _poll("p1.pb", _at(0), 2, arr=20),
        _poll("p2.pb", _at(30), 2, dep=50),
        _poll("p3.pb", _at(60), 2, arr=40),
    ]
    assert _delays(polls) == [(2, 50, 40)]


def test_stop_visits_break_an_arrival_tie_on_file_name():
    polls = [
        _poll("a/000001.pb", _at(0), 2, arr=20),
        _poll("a/000002.pb", _at(0), 2, arr=45),
    ]
    assert _delays(polls) == [(2, None, 45)]


def test_stop_visits_skip_an_implausible_arrival_for_the_earlier_plausible_one():
    """The clamp works on arrivals as it does on departures: an implausible
    reading is dropped before the latest one is picked, so the latest
    plausible reading wins."""
    polls = [
        _poll("p1.pb", _at(0), 2, arr=20, dep=None),
        _poll("p2.pb", _at(30), 2, dep=50),
        _poll("p3.pb", _at(60), 2, arr=MAX_PLAUSIBLE_DELAY_SEC + 1),
        _poll("p4.pb", _at(90), 2, dep=-(MAX_PLAUSIBLE_DELAY_SEC + 1)),
    ]
    assert _delays(polls) == [(2, 50, 20)]


def test_stop_visits_keep_a_departure_whose_only_arrival_is_implausible():
    polls = [
        _poll("p1.pb", _at(0), 2, dep=50),
        _poll("p2.pb", _at(60), 2, arr=MAX_PLAUSIBLE_DELAY_SEC + 1),
    ]
    assert _delays(polls) == [(2, 50, None)]


def test_stop_visits_keep_an_arrival_only_terminus_with_no_departure():
    """The last stop of a trip is only ever reported as an arrival. It is a
    visit all the same: running time into it needs that arrival."""
    polls = [
        _poll("p1.pb", _at(0), 1, dep=30),
        _poll("p1.pb", _at(0), 3, arr=30),
        _poll("p2.pb", _at(60), 1, arr=0),
        _poll("p2.pb", _at(60), 3, arr=45),
    ]
    assert _delays(polls) == [(1, 30, 0), (3, None, 45)]


def test_stop_visits_keep_a_departure_only_visit_with_no_arrival():
    polls = [_poll("p1.pb", _at(0), 2, dep=50), _poll("p2.pb", _at(60), 2, dep=70)]
    assert _delays(polls) == [(2, 70, None)]


def test_stop_visits_drop_a_visit_with_no_plausible_reading():
    polls = [
        _poll("p1.pb", _at(0), 2, dep=MAX_PLAUSIBLE_DELAY_SEC + 1),
        _poll("p2.pb", _at(60), 2, arr=-(MAX_PLAUSIBLE_DELAY_SEC + 1)),
        _poll("p3.pb", _at(90), 3, dep=10),
    ]
    assert _delays(polls) == [(3, 10, None)]


def test_stop_visits_keep_one_row_per_trip_stop_and_service_day():
    """Polls of one stop visit whose schedule columns moved between polls (a
    static reload in the middle of the day) are still one visit, filed under
    the latest departure poll's columns, and its arrival joins that row."""
    polls = [
        _poll("p1.pb", _at(0), 2, dep=50, scheduled_time="10:05", scheduled_sec=36300),
        _poll("p2.pb", _at(30), 2, arr=20, scheduled_time="10:05", scheduled_sec=36300),
        _poll("p3.pb", _at(60), 2, dep=70, scheduled_time="10:06", scheduled_sec=36360, service_type="holiday"),
    ]
    rows = _visits(polls, include_scheduled_sec=True)
    assert [
        (r["service_type"], r["scheduled_time"], r["scheduled_sec"], r["dep_delay"], r["arr_delay"]) for r in rows
    ] == [("holiday", "10:06", 36360, 70, 20)]


def test_stop_visits_file_an_arrival_only_visit_under_its_latest_arrival_poll():
    polls = [
        _poll("p1.pb", _at(0), 3, arr=30, route_code="R1", scheduled_time="10:10"),
        _poll("p2.pb", _at(60), 3, arr=45, route_code="R2", scheduled_time="10:11"),
    ]
    rows = _visits(polls, include_scheduled_sec=True)
    assert [(r["route_code"], r["scheduled_time"], r["scheduled_sec"], r["arr_delay"]) for r in rows] == [
        ("R2", "10:11", None, 45)
    ]


def _observed(polls):
    return [(r["stop_sequence"], r["dep_delay"], r["arr_delay"], r["dep_observed"]) for r in _visits(polls)]


def test_stop_visits_observe_a_departure_reported_after_the_arrival():
    polls = [
        _poll("p1.pb", _at(0), 2, arr=20),
        _poll("p2.pb", _at(60), 2, dep=50),
    ]
    assert _observed(polls) == [(2, 50, 20, True)]


def test_stop_visits_do_not_observe_a_departure_whose_polls_all_precede_the_arrival():
    """The departure still standing when the bus arrived is the feed's last
    prediction of it, not a departure the feed saw."""
    polls = [
        _poll("p1.pb", _at(0), 2, dep=40),
        _poll("p2.pb", _at(30), 2, dep=50),
        _poll("p3.pb", _at(60), 2, arr=20),
    ]
    assert _observed(polls) == [(2, 50, 20, False)]


def test_stop_visits_observe_a_departure_with_a_later_poll_after_the_arrival():
    polls = [
        _poll("p1.pb", _at(0), 2, dep=40),
        _poll("p2.pb", _at(30), 2, arr=20),
        _poll("p3.pb", _at(60), 2, dep=50),
    ]
    assert _observed(polls) == [(2, 50, 20, True)]


def test_stop_visits_judge_the_departure_against_the_first_arrival_poll():
    """A later arrival poll refines the arrival's delay; the departure is
    observed once it was reported after the bus was first reported there."""
    polls = [
        _poll("p1.pb", _at(0), 2, arr=20),
        _poll("p2.pb", _at(30), 2, dep=50),
        _poll("p3.pb", _at(60), 2, arr=25),
    ]
    assert _observed(polls) == [(2, 50, 25, True)]


def test_stop_visits_observe_a_departure_captured_in_the_same_second_as_the_arrival():
    polls = [
        _poll("a/000002.pb", _at(0), 2, arr=20),
        _poll("a/000001.pb", _at(0), 2, dep=50),
    ]
    assert _observed(polls) == [(2, 50, 20, True)]


def test_stop_visits_judge_only_the_plausible_departure_they_keep():
    """An implausible departure after the arrival is dropped, as the dedup
    drops it, so the departure kept is the earlier, pre-arrival one."""
    polls = [
        _poll("p1.pb", _at(0), 2, dep=50),
        _poll("p2.pb", _at(30), 2, arr=20),
        _poll("p3.pb", _at(60), 2, dep=MAX_PLAUSIBLE_DELAY_SEC + 1),
    ]
    assert _observed(polls) == [(2, 50, 20, False)]


def test_stop_visits_judge_against_the_first_plausible_arrival():
    polls = [
        _poll("p1.pb", _at(0), 2, arr=MAX_PLAUSIBLE_DELAY_SEC + 1),
        _poll("p2.pb", _at(30), 2, dep=50),
        _poll("p3.pb", _at(60), 2, arr=20),
    ]
    assert _observed(polls) == [(2, 50, 20, False)]


def test_stop_visits_leave_a_one_sided_visit_unjudged():
    """With no arrival there is nothing to judge a departure against, and with
    no departure nothing to judge: neither is a predicted departure."""
    polls = [
        _poll("p1.pb", _at(0), 2, dep=50),
        _poll("p2.pb", _at(60), 3, arr=30),
    ]
    assert _observed(polls) == [(2, 50, None, None), (3, None, 30, None)]


def test_stop_visits_keep_the_dedups_column_order():
    """analyze() loads the result into a TEMP table by position."""
    rows = _query([_poll("p1.pb", _at(0), 2, dep=50, arr=None)], build_stop_visits_ch_sql())
    assert list(rows[0]) == [
        "route_code",
        "service_type",
        "scheduled_time",
        "trip_id",
        "date",
        "stop_sequence",
        "dep_delay",
        "arr_delay",
        "dep_observed",
    ]
    rows = _query([_poll("p1.pb", _at(0), 2, dep=50)], build_stop_visits_ch_sql(include_scheduled_sec=True))
    assert list(rows[0])[-3:] == ["scheduled_sec", "arr_delay", "dep_observed"]


def _parity_polls():
    """A day of realistic polls with a stable schedule per visit: departure
    predictions for upcoming stops, an arrival poll as the vehicle reaches
    each stop, arrival-only polls for the terminus, a capture-second tie
    broken by file name, an implausible departure spike, a trip that matched
    no service, a trip with no route, and a second service day."""
    polls = []
    for trip_id, route_code, service_type, base in (
        ("T1", "R1", "weekday", _at(0)),
        ("T2", None, None, _at(600)),
        ("T3", "R1", "weekday", _at(86400)),
    ):
        for poll in range(4):
            at = base + timedelta(seconds=60 * poll)
            name = f"{trip_id}/{poll:06d}.pb"
            common = {"trip_id": trip_id, "route_code": route_code, "service_type": service_type}
            for stop in range(poll + 1, 4):
                polls.append(
                    _poll(
                        name,
                        at,
                        stop,
                        dep=30 * poll + stop,
                        scheduled_time=f"10:0{stop}",
                        scheduled_sec=36000 + 60 * stop,
                        **common,
                    )
                )
            if poll:
                polls.append(_poll(name, at, poll, arr=10 * poll, scheduled_time=f"10:0{poll}", **common))
            polls.append(_poll(name, at, 4, arr=poll, scheduled_time="10:04", scheduled_sec=36240, **common))
    # Same capture second as T1's poll 2, which last predicted stop 3's
    # departure; the later file name wins the tie.
    polls.append(_poll("T1/000002b.pb", _at(120), 3, dep=99, scheduled_time="10:03", scheduled_sec=36180))
    # A frozen-feed spike, latest of its visit: dropped, the poll before it wins.
    polls.append(
        _poll("T1/000009.pb", _at(600), 2, dep=MAX_PLAUSIBLE_DELAY_SEC + 1, scheduled_time="10:02", scheduled_sec=36120)
    )
    return polls


@pytest.mark.parametrize("include_scheduled_sec", [False, True])
def test_stop_visits_departures_match_the_shared_dedup_row_for_row(include_scheduled_sec):
    """Wherever a visit has a departure, its row is the shared dedup's row: the
    same key columns, the same winning delay and schedule, the same count of
    visits. Arrival-only visits are the only rows the dedup does not have."""
    polls = _parity_polls()
    dedup_columns = ["route_code", "service_type", "scheduled_time", "trip_id", "date", "stop_sequence", "dep_delay"]
    if include_scheduled_sec:
        dedup_columns.append("scheduled_sec")

    def keyed(rows):
        return sorted((tuple(r[c] for c in dedup_columns) for r in rows), key=repr)

    dedup = _query(polls, build_dedup_ch_sql(include_scheduled_sec=include_scheduled_sec))
    visits = _query(polls, build_stop_visits_ch_sql(include_scheduled_sec=include_scheduled_sec))
    with_departure = [r for r in visits if r["dep_delay"] is not None]

    assert keyed(with_departure) == keyed(dedup)
    assert len(dedup) == 9
    tie_and_spike = {r["stop_sequence"]: r["dep_delay"] for r in dedup if r["trip_id"] == "T1"}
    assert tie_and_spike == {1: 1, 2: 32, 3: 99}
    arrival_only = sorted((r["trip_id"], r["stop_sequence"]) for r in visits if r["dep_delay"] is None)
    assert arrival_only == [("T1", 4), ("T2", 4), ("T3", 4)]


def test_stop_visits_cte_applies_the_range_filter():
    from api.range import RangeCtx
    from pipeline.reports.filters import _stop_visits_cte_ch

    polls = [
        _poll("p1.pb", _at(0), 2, dep=50),
        _poll("p2.pb", _at(60), 2, arr=20),
        _poll("p3.pb", _at(60), 3, arr=30, route_code="R2"),
        _poll("p4.pb", _at(86400), 2, arr=99),
    ]
    ctx = RangeCtx(from_date=date(2026, 4, 1), to_date=date(2026, 4, 1), routes=("R1",))
    cte_sql, params = _stop_visits_cte_ch(ctx)
    rows = _query(
        polls,
        f"WITH {cte_sql} SELECT stop_sequence, dep_delay, arr_delay FROM visits ORDER BY stop_sequence",
        params,
    )
    assert [(r["stop_sequence"], r["dep_delay"], r["arr_delay"]) for r in rows] == [(2, 50, 20)]


def test_stop_visits_read_the_table_they_are_given():
    assert "FROM updates AS u" in build_stop_visits_ch_sql()
    assert "FROM updates_live AS u" in build_stop_visits_ch_sql(table="updates_live")
    with pytest.raises(ValueError):
        build_stop_visits_ch_sql(table="agg_route_daily")
