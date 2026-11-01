"""The passenger delay record's two reads: every trip a route ran on a day,
and one trip's stops in order with the departure delay each one recorded.

A trip that left its first stop on time can still leave a later one late, so
the record is read at the stop the passenger boarded, not the trip's first
recorded stop the staff list keys on."""

from datetime import datetime

import pytest

DAY = "2026-06-24"
DAY_BEFORE = "2026-06-23"


def _static_trip(pg_conn, agency_id, trip_id, stops, *, headsign=None, runs=("2026-06-01", "2026-12-31")):
    """One trip of the loaded timetable under a service of its own that runs
    every day of ``runs``; ``stops`` are ``(stop_sequence, stop_id,
    stop_name, departure_time)``."""
    service_id = f"SVC-{trip_id}"
    with pg_conn.cursor() as cur:
        cur.execute(
            "INSERT INTO static_calendar (agency_id, service_id, monday, tuesday, wednesday, thursday, friday,"
            " saturday, sunday, start_date, end_date)"
            " VALUES (%s, %s, true, true, true, true, true, true, true, %s, %s)",
            (agency_id, service_id, *runs),
        )
        cur.execute(
            "INSERT INTO static_trips (agency_id, trip_id, service_id, trip_headsign) VALUES (%s, %s, %s, %s)",
            (agency_id, trip_id, service_id, headsign),
        )
        for stop_sequence, stop_id, stop_name, departure_time in stops:
            cur.execute(
                "INSERT INTO static_stops (agency_id, stop_id, stop_name) VALUES (%s, %s, %s) ON CONFLICT DO NOTHING",
                (agency_id, stop_id, stop_name),
            )
            cur.execute(
                "INSERT INTO static_stop_times (agency_id, trip_id, stop_sequence, stop_id, departure_time)"
                " VALUES (%s, %s, %s, %s, %s)",
                (agency_id, trip_id, stop_sequence, stop_id, departure_time),
            )
    pg_conn.commit()


def _sec(clock: str) -> int:
    h, m = clock.split(":")[:2]
    return int(h) * 3600 + int(m) * 60


def _reading(trip_id, stop_sequence, dep_delay, scheduled, *, route="R1", day=DAY, poll=0):
    """One poll's departure reading, captured in the evening of ``day`` (JST)
    so it files under that service day; a later ``poll`` is a later capture."""
    captured = datetime.fromisoformat(f"{day}T09:{poll:02d}:00")
    return (
        f"test/{day}/{route}/{trip_id}/{stop_sequence}/{poll}.pb",
        captured,
        trip_id,
        "平日",
        scheduled,
        route,
        stop_sequence,
        dep_delay,
        None,
        None,
        None,
        None,
        None,
        _sec(scheduled),
    )


TIMETABLE = [
    (1, "S1", "広島バスセンター", "18:00:00"),
    (2, "S2", "紙屋町東", "18:04:00"),
    (3, "S3", "八丁堀", "18:08:00"),
    (4, "S4", "熊野萩原", "18:40:00"),
]


@pytest.fixture
def record_app(ch_async_client):
    from api.main import app

    app.state.ch_client = ch_async_client
    return app


@pytest.mark.asyncio
async def test_trips_lists_every_trip_the_route_ran_that_day_on_time_ones_too(
    client, agency_id, pg_conn, ch_client, record_app
):
    from pipeline.clickhouse import insert_updates

    _static_trip(
        pg_conn, agency_id, "T-ON", [(1, "S1", "広島バスセンター", "07:00:00"), (2, "S2", "紙屋町東", "07:04:00")]
    )
    _static_trip(pg_conn, agency_id, "T-LATE", TIMETABLE, headsign="熊野営業所")
    insert_updates(
        ch_client,
        agency_id,
        [
            _reading("T-LATE", 1, 0, "18:00:00"),
            _reading("T-LATE", 3, 480, "18:08:00"),
            _reading("T-ON", 1, 0, "07:00:00"),
            _reading("T-ON", 2, 0, "07:04:00"),
            _reading("T-OTHER-ROUTE", 1, 600, "09:00:00", route="R2"),
            _reading("T-YESTERDAY", 1, 600, "09:00:00", day=DAY_BEFORE),
        ],
    )

    resp = await client.get(f"/api/{agency_id}/delay_record/trips?date={DAY}&route=R1")
    assert resp.status_code == 200
    body = resp.json()
    assert (body["date"], body["route"]) == (DAY, "R1")
    assert body["trips"] == [
        {
            "trip_id": "T-ON",
            "departure_sec": _sec("07:00"),
            "terminal": "紙屋町東",
            "headsign": None,
            "matched": True,
            "observed_stops": 2,
        },
        {
            "trip_id": "T-LATE",
            "departure_sec": _sec("18:00"),
            "terminal": "熊野萩原",
            "headsign": "熊野営業所",
            "matched": True,
            "observed_stops": 2,
        },
    ]


@pytest.mark.asyncio
async def test_trips_does_not_call_the_earliest_recorded_time_the_origin_when_the_timetable_does_not_run_the_trip(
    client, agency_id, pg_conn, ch_client, record_app
):
    """The loaded timetable is replaced in place on a reload, so before it
    starts it cannot say where a trip began or where it ran to."""
    from pipeline.clickhouse import insert_updates

    _static_trip(pg_conn, agency_id, "T-RELOADED", TIMETABLE, headsign="熊野営業所", runs=("2026-06-25", "2026-12-31"))
    insert_updates(
        ch_client, agency_id, [_reading("T-RELOADED", 3, 300, "18:08:00"), _reading("T-RELOADED", 2, 240, "18:04:00")]
    )

    resp = await client.get(f"/api/{agency_id}/delay_record/trips?date={DAY}&route=R1")
    assert resp.status_code == 200
    assert resp.json()["trips"] == [
        {
            "trip_id": "T-RELOADED",
            "departure_sec": _sec("18:04"),
            "terminal": None,
            "headsign": None,
            "matched": False,
            "observed_stops": 2,
        }
    ]


@pytest.mark.asyncio
async def test_trips_is_empty_for_a_route_with_no_reading_that_day(client, agency_id, ch_client, record_app):
    from pipeline.clickhouse import insert_updates

    insert_updates(ch_client, agency_id, [_reading("T-ELSEWHERE", 1, 60, "18:00:00", route="R2")])
    resp = await client.get(f"/api/{agency_id}/delay_record/trips?date={DAY}&route=R1")
    assert resp.status_code == 200
    assert resp.json()["trips"] == []


@pytest.mark.asyncio
async def test_stops_lists_the_trips_stops_in_order_with_each_ones_latest_plausible_departure_reading(
    client, agency_id, pg_conn, ch_client, record_app
):
    """The same rule as every other surface: the latest poll of each stop,
    with an implausible reading dropped. A stop with no reading is listed
    with none rather than given one."""
    from pipeline.clickhouse import insert_updates

    _static_trip(pg_conn, agency_id, "T-LATE", TIMETABLE, headsign="熊野営業所")
    insert_updates(
        ch_client,
        agency_id,
        [
            _reading("T-LATE", 1, 0, "18:00:00"),
            _reading("T-LATE", 2, 120, "18:04:00", poll=1),
            _reading("T-LATE", 2, 240, "18:04:00", poll=5),
            _reading("T-LATE", 3, 9000, "18:08:00"),
            _reading("T-LATE", 2, 999, "18:04:00", day=DAY_BEFORE),
            _reading("T-LATE", 3, 999, "18:08:00", route="R2"),
        ],
    )

    resp = await client.get(f"/api/{agency_id}/delay_record/stops?date={DAY}&route=R1&trip=T-LATE")
    assert resp.status_code == 200
    body = resp.json()
    assert body["agency_name"] == "テスト交通"
    assert body["trip"] == {
        "trip_id": "T-LATE",
        "departure_sec": _sec("18:00"),
        "terminal": "熊野萩原",
        "headsign": "熊野営業所",
        "matched": True,
        "observed_stops": 2,
    }
    assert body["stops"] == [
        {"stop_sequence": 1, "stop_name": "広島バスセンター", "scheduled_sec": _sec("18:00"), "dep_delay": 0},
        {"stop_sequence": 2, "stop_name": "紙屋町東", "scheduled_sec": _sec("18:04"), "dep_delay": 240},
        {"stop_sequence": 3, "stop_name": "八丁堀", "scheduled_sec": _sec("18:08"), "dep_delay": None},
        {"stop_sequence": 4, "stop_name": "熊野萩原", "scheduled_sec": _sec("18:40"), "dep_delay": None},
    ]


@pytest.mark.asyncio
async def test_stops_of_a_trip_the_timetable_does_not_run_that_day_are_its_recorded_stops_unnamed(
    client, agency_id, pg_conn, ch_client, record_app
):
    from pipeline.clickhouse import insert_updates

    _static_trip(pg_conn, agency_id, "T-RELOADED", TIMETABLE, runs=("2026-06-25", "2026-12-31"))
    insert_updates(
        ch_client, agency_id, [_reading("T-RELOADED", 3, 300, "18:08:00"), _reading("T-RELOADED", 2, 240, "18:04:00")]
    )

    resp = await client.get(f"/api/{agency_id}/delay_record/stops?date={DAY}&route=R1&trip=T-RELOADED")
    assert resp.status_code == 200
    body = resp.json()
    assert body["trip"]["matched"] is False
    assert body["stops"] == [
        {"stop_sequence": 2, "stop_name": None, "scheduled_sec": _sec("18:04"), "dep_delay": 240},
        {"stop_sequence": 3, "stop_name": None, "scheduled_sec": _sec("18:08"), "dep_delay": 300},
    ]


@pytest.mark.asyncio
async def test_stops_of_a_trip_with_no_reading_that_day_has_no_trip(client, agency_id, pg_conn, ch_client, record_app):
    from pipeline.clickhouse import insert_updates

    _static_trip(pg_conn, agency_id, "T-QUIET", TIMETABLE)
    insert_updates(ch_client, agency_id, [_reading("T-QUIET", 1, 60, "18:00:00", day=DAY_BEFORE)])

    resp = await client.get(f"/api/{agency_id}/delay_record/stops?date={DAY}&route=R1&trip=T-QUIET")
    assert resp.status_code == 200
    assert (resp.json()["trip"], resp.json()["stops"]) == (None, [])


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "query",
    [
        "delay_record/trips?date=2026-06-24",
        "delay_record/trips?route=R1",
        "delay_record/trips?date=not-a-day&route=R1",
        "delay_record/stops?date=2026-06-24&route=R1",
        "delay_record/stops?date=2026-06-24&trip=T1",
    ],
)
async def test_record_reads_need_a_day_a_route_and_for_stops_a_trip(client, agency_id, record_app, query):
    resp = await client.get(f"/api/{agency_id}/{query}")
    assert resp.status_code == 422
