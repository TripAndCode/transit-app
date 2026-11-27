"""Reading an archived timetable on a typical day of each day type, and the
departures it plans there at every stop.

Shared by the timetable revision comparison (pipeline.reports.timetable_diff)
and the planned stop supply (pipeline.stop_supply), so the two pick a typical
day by the same rule and count the same departures on it, in service-day
seconds. They seek the sample dates in different places: the comparison
nearest its revision, the supply from where a version comes into force.

Day types follow the weekday rule (pipeline.day_types.fallback_day_type), the
same three for every feed. A typical day is, among sample dates of a type, the
one whose set of running services most of them share, so a holiday-timetable
weekday, the year-end break or a one-off addition does not stand for the type.
A type with no sample date within the calendar's extent is unknown, never
zero; a date within the extent that runs nothing is a known empty day.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable, Mapping, Sequence
from datetime import date
from typing import Literal, NamedTuple, cast

from pipeline import static_archive
from pipeline.day_types import (
    ServiceCalendar,
    WeeklyService,
    calendar_dates_day,
    fallback_day_type,
    public_holidays_between,
)
from pipeline.service_day import service_day_offset_ch

DayType = Literal["weekday", "saturday", "sunday_holiday"]
DAY_TYPES: tuple[DayType, ...] = ("weekday", "saturday", "sunday_holiday")

# Five weeks of a type outvote a holiday, the year-end break (a few weekdays)
# or a one-off addition near where the dates are sought.
SAMPLE_DATES = 5
_WEEKDAYS = range(5)
# How far dates are sought, which bounds the walk over a version whose span
# runs open-ended.
MAX_WALK_DAYS = 400


class TypicalDay(NamedTuple):
    day: date
    services: frozenset[str]


def service_calendar(archived: static_archive.ArchivedCalendar) -> ServiceCalendar:
    """A kept calendar as the running rule reads it. A calendar_dates row
    whose date is not a GTFS date names no day."""
    weekly = [
        WeeklyService(row[0], tuple(bool(flag) for flag in row[1:8]), row[8], row[9]) for row in archived.calendar
    ]
    exceptions = [
        (service_id, day, exception)
        for service_id, raw, exception in archived.calendar_dates
        if (day := calendar_dates_day(raw)) is not None and exception is not None
    ]
    return ServiceCalendar.of(weekly, exceptions)


def typical_day(dates: Sequence[date], calendar: ServiceCalendar) -> TypicalDay | None:
    """The set of services most of *dates* run, on the first of *dates* that
    runs it; None for no dates. Sets tied on count go to the one met first, so
    *dates* come in the order they were sought. An empty set is a set like any
    other: a type whose dates mostly run nothing plans no departure."""
    if not dates:
        return None
    running = [(day, calendar.running(day)) for day in dates]
    counts = Counter(services for _day, services in running)
    best = max(counts.values())
    return next(TypicalDay(day, services) for day, services in running if counts[services] == best)


def _quota(day_type: DayType, day: date) -> tuple[DayType, int | None]:
    return (day_type, day.weekday()) if day_type == "weekday" else (day_type, None)


def typical_days(
    walk: Iterable[date], calendar: ServiceCalendar, holidays_between: tuple[date, date]
) -> dict[DayType, TypicalDay | None]:
    """The typical day of each day type among the dates of *walk*, taken in
    its order within *calendar*'s extent: up to SAMPLE_DATES of each weekday
    Monday to Friday for the weekday type, up to SAMPLE_DATES for each other
    type. *holidays_between* bounds the public holidays the weekday rule
    reads, and must cover every date of *walk*. The walk stops once every
    quota is met."""
    holidays = frozenset(public_holidays_between(*holidays_between))
    left: dict[tuple[DayType, int | None], int] = {("weekday", weekday): SAMPLE_DATES for weekday in _WEEKDAYS}
    left |= {("saturday", None): SAMPLE_DATES, ("sunday_holiday", None): SAMPLE_DATES}
    picked: dict[DayType, list[date]] = {day_type: [] for day_type in DAY_TYPES}
    for day in walk:
        if not any(left.values()):
            break
        # The weekday rule names only the three types read here.
        day_type = cast(DayType, fallback_day_type(day, holidays))
        quota = _quota(day_type, day)
        if left[quota] and calendar.covers(day):
            picked[day_type].append(day)
            left[quota] -= 1
    return {day_type: typical_day(dates, calendar) for day_type, dates in picked.items()}


def stop_departures(*, trips: bool = False) -> str:
    """Each departure a typical day plans, one row each: its day type's index,
    its stop and its service-day second (pipeline.service_day), so 00:40
    written at face value is the same night's 24:40; with *trips*, its trip
    and route as well (NULL where the timetable names none).

    A trip's last stop is where it arrives, so it departs nowhere; an untimed
    stop time departs at no known hour. {services} holds each day type's
    running services, in DAY_TYPES order."""
    outer = ", trip_id, route_id" if trips else ""
    inner = " s.trip_id AS trip_id, s.route_id AS route_id," if trips else ""
    # Each day type's list is indexed by a constant. Indexed inside a lambda,
    # ClickHouse copies the list onto every departure row, and a timetable
    # running hundreds of services on one day type outgrows the server's memory.
    day_types = ", ".join(
        f"if(has({{services:Array(Array(String))}}[{index}], service), {index}, 0)"
        for index in range(1, len(DAY_TYPES) + 1)
    )
    return f"""
    SELECT dt, stop_id, sec{outer}
    FROM (
        SELECT st.stop_id AS stop_id,{inner} assumeNotNull(s.service_type) AS service,
               assumeNotNull({service_day_offset_ch("s.scheduled_sec")}) AS sec
        FROM {static_archive.SCHEDULE} AS s
        INNER JOIN (
            SELECT trip_id, stop_sequence, stop_id,
                   max(stop_sequence) OVER (PARTITION BY trip_id) AS last_sequence
            FROM {static_archive.ARCHIVE}
            WHERE agency_id = {{agency:Int32}} AND content_hash = {{content:String}}
        ) AS st ON st.trip_id = s.trip_id AND st.stop_sequence = s.stop_sequence
        WHERE s.agency_id = {{agency:Int32}} AND s.content_hash = {{content:String}}
          AND s.service_type IS NOT NULL AND s.scheduled_sec IS NOT NULL
          AND st.stop_sequence < st.last_sequence
    )
    ARRAY JOIN arrayFilter(i -> i > 0, [{day_types}]) AS dt
"""


_STOP_DEPARTURES = stop_departures()

# One row per day type and stop, its departures in an array.
DEPARTURE_SECONDS_SQL = f"SELECT dt, stop_id, groupArray(sec) FROM ({_STOP_DEPARTURES}) GROUP BY dt, stop_id"
# One row per day type and stop, its service-day hours' (hour, departures,
# first second, last second) in an array, so the result stays a few times the
# stop count, within the API client's max_result_rows.
DEPARTURE_HOURS_SQL = f"""
    SELECT dt, stop_id, groupArray((hour, departures, first_sec, last_sec))
    FROM (
        SELECT dt, stop_id, intDiv(sec, 3600) AS hour, toInt64(count()) AS departures,
               min(sec) AS first_sec, max(sec) AS last_sec
        FROM ({_STOP_DEPARTURES})
        GROUP BY dt, stop_id, hour
    )
    GROUP BY dt, stop_id
"""


def services_by_day_type(days: Mapping[DayType, TypicalDay | None]) -> list[list[str]]:
    """The {services} parameter of the departures SQL: each day type's running
    services on its typical day, none for an unknown type."""
    return [sorted(typical.services) if (typical := days.get(day_type)) is not None else [] for day_type in DAY_TYPES]
