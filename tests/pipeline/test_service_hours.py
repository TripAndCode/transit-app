"""A service day's hours, as the loaded timetable plans them.

They run from the first departure of the trips the calendar runs that day to
the last arrival of those trips. Times past midnight belong to the evening
before, whether the feed writes them as GTFS's extended hours (``24:30``) or at
face value (``00:40``).

April 2026 starts on a Wednesday: the 6th is a Monday, the 11th a Saturday.
"""

from datetime import date, datetime, timedelta, timezone

import pytest

from pipeline.service_day import SERVICE_DAY_CUTOVER_HOURS, service_day_seconds, service_day_seconds_sql
from pipeline.service_hours import ServiceHours, load_service_hours

JST = timezone(timedelta(hours=9))
MONDAY = date(2026, 4, 6)
TUESDAY = date(2026, 4, 7)
SATURDAY = date(2026, 4, 11)


def _jst(day: date, hour: int, minute: int) -> datetime:
    return datetime(day.year, day.month, day.day, tzinfo=JST) + timedelta(hours=hour, minutes=minute)


async def _weekly(conn, agency_id: int, service_id: str, days: str, start=date(2026, 4, 1), end=date(2026, 4, 30)):
    """One ``calendar.txt`` row; ``days`` is its Monday-first ``"1111100"`` flags."""
    await conn.execute(
        "INSERT INTO static_calendar (agency_id, service_id, monday, tuesday, wednesday, thursday, friday, "
        "saturday, sunday, start_date, end_date) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11)",
        agency_id,
        service_id,
        *(flag == "1" for flag in days),
        start,
        end,
    )


async def _exception(conn, agency_id: int, service_id: str, day: date, exception_type: int):
    await conn.execute(
        "INSERT INTO static_calendar_dates (agency_id, service_id, date, exception_type) VALUES ($1, $2, $3, $4)",
        agency_id,
        service_id,
        day.strftime("%Y%m%d"),
        exception_type,
    )


async def _trip(conn, agency_id: int, service_id: str, trip_id: str, stops: list[tuple[str | None, str | None]]):
    """One trip under ``service_id``; ``stops`` are its (arrival, departure) times in stop order."""
    await conn.execute(
        "INSERT INTO static_trips (agency_id, trip_id, route_id, service_id) VALUES ($1, $2, 'R1', $3)",
        agency_id,
        trip_id,
        service_id,
    )
    for sequence, (arrival, departure) in enumerate(stops, start=1):
        await conn.execute(
            "INSERT INTO static_stop_times (agency_id, trip_id, stop_sequence, stop_id, arrival_time, departure_time) "
            "VALUES ($1, $2, $3, $4, $5, $6)",
            agency_id,
            trip_id,
            sequence,
            f"S{sequence}",
            arrival,
            departure,
        )


@pytest.mark.parametrize(
    ("hour", "minute", "second"),
    [
        (0, 0, 0),
        (0, 10, 0),
        (SERVICE_DAY_CUTOVER_HOURS - 1, 59, 59),
        (SERVICE_DAY_CUTOVER_HOURS, 0, 0),
        (12, 0, 0),
        (23, 59, 59),
        (24, 10, 0),
        (27, 59, 0),
    ],
)
async def test_the_sql_reads_a_time_into_the_service_day_as_the_python_does(aconn, hour, minute, second):
    seconds = hour * 3600 + minute * 60 + second
    got = await aconn.fetchval(f"SELECT {service_day_seconds_sql('$1::int')}", seconds)
    assert got == service_day_seconds(hour, minute, second)


async def test_the_sql_keeps_an_unreadable_time_unreadable(aconn):
    assert await aconn.fetchval(f"SELECT {service_day_seconds_sql('$1::int')}", None) is None


async def test_hours_run_from_the_first_departure_to_the_last_arrival_of_the_trips_running_that_day(aconn, aagency_id):
    await _weekly(aconn, aagency_id, "WD", "1111100")
    await _weekly(aconn, aagency_id, "SA", "0000010")
    # The first stop is reached at 06:03 but left at 06:05; the last is reached
    # at 21:48 and left at 21:50. A trip starts when it leaves and ends when it
    # arrives.
    await _trip(aconn, aagency_id, "WD", "early", [("06:03:00", "06:05:00"), ("06:20:00", "06:20:00")])
    await _trip(aconn, aagency_id, "WD", "late", [("21:00:00", "21:00:00"), (" 21:48:00 ", "21:50:00")])
    # A first stop the feed gives no readable time leaves that trip's start out
    # rather than reading it as midnight; its end still counts.
    await _trip(aconn, aagency_id, "WD", "untimed", [("", ""), ("bad", None), ("12:00:00", "12:00:00")])
    # Only a trip's first and last stops date it.
    await _trip(aconn, aagency_id, "WD", "middle", [("07:00:00", "07:00:00"), ("x", "05:00"), ("08:00:00", None)])
    await _trip(aconn, aagency_id, "SA", "saturday", [("5:00:00", "5:00:00"), ("23:30:00", "23:30:00")])

    assert await load_service_hours(aconn, aagency_id, MONDAY) == ServiceHours(
        first_departure_at=_jst(MONDAY, 6, 5).astimezone(timezone.utc),
        last_arrival_at=_jst(MONDAY, 21, 48).astimezone(timezone.utc),
    )
    assert await load_service_hours(aconn, aagency_id, SATURDAY) == ServiceHours(
        first_departure_at=_jst(SATURDAY, 5, 0).astimezone(timezone.utc),
        last_arrival_at=_jst(SATURDAY, 23, 30).astimezone(timezone.utc),
    )


async def test_the_instants_are_utc(aconn, aagency_id):
    await _weekly(aconn, aagency_id, "ALL", "1111111")
    await _trip(aconn, aagency_id, "ALL", "t", [("08:00:00", "08:00:00"), ("09:00:00", "09:00:00")])
    hours = await load_service_hours(aconn, aagency_id, MONDAY)
    assert hours is not None
    assert hours.first_departure_at.utcoffset() == timedelta(0)
    assert hours.last_arrival_at.utcoffset() == timedelta(0)


async def test_a_trip_past_midnight_ends_the_service_day_after_its_date(aconn, aagency_id):
    await _weekly(aconn, aagency_id, "ALL", "1111111")
    await _trip(aconn, aagency_id, "ALL", "morning", [("06:00:00", "06:00:00"), ("07:00:00", "07:00:00")])
    await _trip(aconn, aagency_id, "ALL", "extended", [("23:50:00", "23:50:00"), ("24:30:00", "24:30:00")])
    # Written at face value, 00:40 is the same night's, later than 24:30 and
    # never the morning's first departure.
    await _trip(aconn, aagency_id, "ALL", "face", [("23:55:00", "23:55:00"), ("00:40:00", "00:40:00")])

    hours = await load_service_hours(aconn, aagency_id, MONDAY)

    assert hours == ServiceHours(
        first_departure_at=_jst(MONDAY, 6, 0).astimezone(timezone.utc),
        last_arrival_at=_jst(MONDAY, 24, 40).astimezone(timezone.utc),
    )


async def test_calendar_dates_add_and_remove_the_services_that_run(aconn, aagency_id):
    await _weekly(aconn, aagency_id, "WD", "1111100")
    await _trip(aconn, aagency_id, "WD", "weekday", [("06:00:00", "06:00:00"), ("22:00:00", "22:00:00")])
    await _trip(aconn, aagency_id, "HOL", "holiday", [("08:00:00", "08:00:00"), ("18:00:00", "18:00:00")])
    await _exception(aconn, aagency_id, "WD", TUESDAY, 2)
    await _exception(aconn, aagency_id, "HOL", TUESDAY, 1)

    assert await load_service_hours(aconn, aagency_id, TUESDAY) == ServiceHours(
        first_departure_at=_jst(TUESDAY, 8, 0).astimezone(timezone.utc),
        last_arrival_at=_jst(TUESDAY, 18, 0).astimezone(timezone.utc),
    )


async def test_no_hours_when_the_timetable_runs_nothing_that_day(aconn, aagency_id):
    await _weekly(aconn, aagency_id, "WD", "1111100")
    await _trip(aconn, aagency_id, "WD", "weekday", [("06:00:00", "06:00:00"), ("22:00:00", "22:00:00")])

    # Covered with nothing running, and a day past the calendar's end.
    assert await load_service_hours(aconn, aagency_id, SATURDAY) is None
    assert await load_service_hours(aconn, aagency_id, date(2026, 5, 4)) is None


async def test_no_hours_without_a_timetable(aconn, aagency_id):
    assert await load_service_hours(aconn, aagency_id, MONDAY) is None


async def test_another_agencys_timetable_does_not_widen_the_hours(aconn, aagency_id):
    other = await aconn.fetchval(
        "INSERT INTO agencies (agency_name, feed_url) VALUES ('other', 'http://other') RETURNING agency_id"
    )
    for agency in (aagency_id, other):
        await _weekly(aconn, agency, "ALL", "1111111")
    await _trip(aconn, aagency_id, "ALL", "mine", [("07:00:00", "07:00:00"), ("20:00:00", "20:00:00")])
    await _trip(aconn, other, "ALL", "theirs", [("05:00:00", "05:00:00"), ("24:30:00", "24:30:00")])

    assert await load_service_hours(aconn, aagency_id, MONDAY) == ServiceHours(
        first_departure_at=_jst(MONDAY, 7, 0).astimezone(timezone.utc),
        last_arrival_at=_jst(MONDAY, 20, 0).astimezone(timezone.utc),
    )
