"""Day types: which timetable class a service date runs.

A date's day type comes from the agency's own static calendar. The services
that run on it are the ``calendar.txt`` rows whose range covers it and whose
weekly flag for its weekday is set, plus its ``calendar_dates.txt`` additions,
minus its removals. Each service is classed by the weekdays its weekly flags
name (:func:`service_class`), and the date takes the class running the most
trips. Reading the calendar rather than the weekday catches the days a feed
runs another timetable: a Saturday on the holiday timetable, a public holiday,
the year-end break. Reading it rather than each observation's own
``service_id`` keeps the observations that never matched a scheduled trip,
whose ``service_type`` is empty, and needs no parsing of service names, which
every feed spells differently.

A date the calendar says nothing about (no calendar loaded, outside every
range, or only unclassed services running) falls back to the weekday rule
(:func:`fallback_day_type`).

Which buckets an agency is shown in follows its calendar too
(:func:`resolve_day_types`): a feed with one timetable for Saturday and Sunday
alike gets 平日 / 土日祝, every other feed 平日 / 土曜 / 日祝.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Collection, Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Literal

import jpholiday

DayType = Literal["weekday", "saturday", "sunday_holiday", "weekend_holiday"]

# The buckets an agency is shown in, in display order.
THREE_WAY: tuple[DayType, ...] = ("weekday", "saturday", "sunday_holiday")
TWO_WAY: tuple[DayType, ...] = ("weekday", "weekend_holiday")

_ORDER: tuple[DayType, ...] = ("weekday", "saturday", "sunday_holiday", "weekend_holiday")


def public_holidays_between(from_date: date, to_date: date) -> list[date]:
    """Japan's public holidays in ``[from_date, to_date]``, oldest first.

    Includes substitute holidays (振替休日) and citizens' holidays (国民の休日).
    """
    return sorted(d for d, _name in jpholiday.between(from_date, to_date))


def fallback_day_type(d: date, holidays: Collection[date] | None = None) -> DayType:
    """The weekday rule: Sunday or a public holiday is 日祝, Saturday 土曜, else 平日.

    A public holiday counts as 日祝 whatever weekday it falls on, a Saturday
    included. ``holidays`` is the window's holiday list when the caller already
    holds it; without it the holiday calendar is consulted directly.
    """
    weekday = d.isoweekday()
    if weekday == 7 or (d in holidays if holidays is not None else jpholiday.is_holiday(d)):
        return "sunday_holiday"
    if weekday == 6:
        return "saturday"
    return "weekday"


def service_class(days: Sequence[bool]) -> DayType | None:
    """The class of a service from its weekly flags, Monday first.

    Weekdays only is 平日, Saturday only 土曜, Sunday only 日祝, and Saturday
    with Sunday 土日祝. Flags that mix weekdays with the weekend, or name no day
    at all (a service run only on dates ``calendar_dates.txt`` adds), class
    nothing, so such a service has no say in a date's day type.
    """
    weekdays, saturday, sunday = any(days[:5]), days[5], days[6]
    if weekdays:
        return None if saturday or sunday else "weekday"
    if saturday and sunday:
        return "weekend_holiday"
    if saturday:
        return "saturday"
    if sunday:
        return "sunday_holiday"
    return None


@dataclass(frozen=True)
class WeeklyService:
    """One ``calendar.txt`` row."""

    service_id: str
    days: tuple[bool, ...]  # Monday first, the order of ``date.weekday()``
    start: date
    end: date


@dataclass(frozen=True)
class DayTypes:
    """Each date of a window filed under one of ``buckets``."""

    buckets: tuple[DayType, ...]
    by_date: Mapping[date, DayType]

    def of(self, d: date) -> DayType:
        return self.by_date[d]


def _majority(
    running: Iterable[str], classes: Mapping[str, DayType | None], trips: Mapping[str, int], fallback: DayType
) -> DayType:
    """The class running the most trips, then the most services.

    A tie goes to the tied class that agrees with ``fallback``, the weekday
    rule's verdict; 土日祝 agrees with both 土曜 and 日祝, so a two-class feed
    breaks a tie the way a three-class one does. Any other tie goes to the
    first in display order.
    """
    votes: dict[DayType, list[int]] = {}
    for service_id in running:
        cls = classes.get(service_id)
        if cls is not None:
            slot = votes.setdefault(cls, [0, 0])
            slot[0] += trips.get(service_id, 0)
            slot[1] += 1
    if not votes:
        return fallback
    best = max(votes.values())
    tied = [cls for cls in _ORDER if votes.get(cls) == best]
    return next((cls for cls in tied if _agrees(cls, fallback)), tied[0])


def _agrees(a: DayType, b: DayType) -> bool:
    """Whether two classes name the same kind of day, 土日祝 standing for either weekend class."""
    weekend = {"saturday", "sunday_holiday", "weekend_holiday"}
    return a == b or ("weekend_holiday" in (a, b) and a in weekend and b in weekend)


def resolve_day_types(
    from_date: date,
    to_date: date,
    weekly: Iterable[WeeklyService],
    exceptions: Iterable[tuple[str, date, int]],
    trips: Mapping[str, int],
) -> DayTypes:
    """File every date of ``[from_date, to_date]`` under a bucket.

    ``exceptions`` are ``calendar_dates.txt`` rows as ``(service_id, date,
    exception_type)``: type 1 adds the service on that date, type 2 removes it,
    and a removal wins over the weekly flags. ``trips`` is each service's trip
    count, the weight of its vote.

    The buckets are 平日 / 土日祝 when the calendar's services include a
    Saturday-and-Sunday class but no Saturday-only one, and 平日 / 土曜 / 日祝
    otherwise, including when no calendar is loaded. The services read are
    those overlapping the window, or every loaded one when none does (a window
    before or after the loaded feed's range). They are read from the services
    rather than from the dates the window resolved to, so a window of weekdays
    alone, or dates outside the calendar's range, cannot change how an agency
    is split. Under two buckets a 土曜 or 日祝 date
    (from the weekday rule, or a feed's Sunday-only service) folds into 土日祝;
    under three a 土日祝 date is 土曜 on a Saturday that is not a public holiday
    and 日祝 otherwise.
    """
    weekly = list(weekly)
    classes: dict[str, DayType | None] = {s.service_id: service_class(s.days) for s in weekly}
    in_window = [s for s in weekly if s.start <= to_date and s.end >= from_date]
    added: dict[date, set[str]] = defaultdict(set)
    removed: dict[date, set[str]] = defaultdict(set)
    for service_id, d, exception_type in exceptions:
        if exception_type == 1:
            added[d].add(service_id)
        elif exception_type == 2:
            removed[d].add(service_id)

    window_classes = {classes[s.service_id] for s in in_window or weekly}
    window_classes |= {classes.get(sid) for d, sids in added.items() if from_date <= d <= to_date for sid in sids}
    two_way = "weekend_holiday" in window_classes and "saturday" not in window_classes

    holidays = frozenset(public_holidays_between(from_date, to_date))
    by_date: dict[date, DayType] = {}
    d = from_date
    while d <= to_date:
        fallback = fallback_day_type(d, holidays)
        running = {s.service_id for s in in_window if s.start <= d <= s.end and s.days[d.weekday()]}
        running = (running | added.get(d, set())) - removed.get(d, set())
        cls = _majority(running, classes, trips, fallback)
        if two_way:
            by_date[d] = "weekday" if cls == "weekday" else "weekend_holiday"
        elif cls == "weekend_holiday":
            by_date[d] = "saturday" if fallback == "saturday" else "sunday_holiday"
        else:
            by_date[d] = cls
        d += timedelta(days=1)
    return DayTypes(buckets=TWO_WAY if two_way else THREE_WAY, by_date=by_date)


def _gtfs_date(value: str) -> date | None:
    """A ``calendar_dates.txt`` ``YYYYMMDD`` value, None when malformed (stored as the feed's raw text).

    Only eight ASCII digits count: ``strptime`` alone would also read ``2026041``,
    which the reports' SQL, matching the text as ``YYYYMMDD``, never does.
    """
    if not (isinstance(value, str) and len(value) == 8 and value.isascii() and value.isdigit()):
        return None
    try:
        return datetime.strptime(value, "%Y%m%d").date()
    except (TypeError, ValueError):
        return None


async def load_day_types(conn, agency_id: int, from_date: date, to_date: date) -> DayTypes:
    """:func:`resolve_day_types` over the agency's loaded static calendar."""
    weekly_rows = await conn.fetch(
        "SELECT service_id, monday, tuesday, wednesday, thursday, friday, saturday, sunday, start_date, end_date, "
        "trips FROM static_calendar WHERE agency_id = $1",
        agency_id,
    )
    # The YYYYMMDD text compares in calendar order, so BETWEEN bounds it
    # without a cast that a malformed row would fail.
    exception_rows = await conn.fetch(
        "SELECT service_id, date, exception_type FROM static_calendar_dates "
        "WHERE agency_id = $1 AND date BETWEEN $2 AND $3",
        agency_id,
        from_date.strftime("%Y%m%d"),
        to_date.strftime("%Y%m%d"),
    )
    weekly = [
        WeeklyService(
            r["service_id"],
            (r["monday"], r["tuesday"], r["wednesday"], r["thursday"], r["friday"], r["saturday"], r["sunday"]),
            r["start_date"],
            r["end_date"],
        )
        for r in weekly_rows
    ]
    exceptions = [
        (r["service_id"], d, r["exception_type"]) for r in exception_rows if (d := _gtfs_date(r["date"])) is not None
    ]
    # Only services with a weekly class vote, and those all have a calendar.txt row.
    trips = {r["service_id"]: r["trips"] for r in weekly_rows}
    return resolve_day_types(from_date, to_date, weekly, exceptions, trips)
