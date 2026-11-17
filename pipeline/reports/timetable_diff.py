"""Two timetables compared stop by stop: the departures each plans at every
stop, hour and day type, and what a revision lost or gained there.

The timetables are the archive's (pipeline.static_archive): the versions in
force over the agency's service dates (pipeline.timetable_in_force), and each
version's kept stop times, schedule and calendar. A revision is a service date
from which another timetable is in force than on the day before: other
content, or the same content under another calendar, since the content does
not cover the calendar and a reissue that only drops a day type's services is
a cut all the same.

Each side of a revision is read on one typical day per day type. Day types
follow the weekday rule (pipeline.day_types.fallback_day_type), the same three
for every feed, so the two sides always split alike whatever classes either
calendar names. The typical day is, among the dates of that type near the
revision, the one whose set of running services most of them share, so a
holiday-timetable weekday, the year-end break or a one-off addition near the
boundary does not stand for the type. A side with no date of a type in force
within its calendar's extent has that type unknown, which is never read as
zero; a date within the extent that runs nothing is a known empty day.

The definitions a reader sees are in docs/methodology.md (時刻表の改正の比較).
"""

from __future__ import annotations

import asyncio
from collections import Counter
from collections.abc import Iterable, Mapping, Sequence
from datetime import date, timedelta
from enum import Enum
from itertools import pairwise
from typing import Literal, NamedTuple, cast

from pipeline import static_archive
from pipeline.cache import async_lru_cache, cache_briefly
from pipeline.day_types import (
    ServiceCalendar,
    WeeklyService,
    calendar_dates_day,
    fallback_day_type,
    public_holidays_between,
)

RevisionDayType = Literal["weekday", "saturday", "sunday_holiday"]
DAY_TYPES: tuple[RevisionDayType, ...] = ("weekday", "saturday", "sunday_holiday")

# Five weeks of a type outvote a holiday, the year-end break (a few weekdays)
# or a one-off addition near the revision (typical_days).
SAMPLE_DATES = 5
_WEEKDAYS = range(5)
# How far from the revision a side is walked for its sample dates, which
# bounds the walk over a version whose span runs open-ended.
_MAX_WALK_DAYS = 400


class Run(NamedTuple):
    """Consecutive service dates, both ends inclusive, over which one
    timetable is in force: one content under one calendar, whichever names it
    was loaded under. *stem* is a name in force in the run, one whose kept
    calendar the run reads where any is kept; *calendar* that calendar's
    digest, None when none is kept."""

    first: date
    last: date
    stem: str
    content: str
    calendar: str | None


def _one_timetable(content: str, calendar: str | None, other_content: str, other_calendar: str | None) -> bool:
    """Whether two versions are one timetable: one content, and one calendar
    unless either calendar is not kept, which tells no difference."""
    return content == other_content and (calendar == other_calendar or calendar is None or other_calendar is None)


def runs(segments: Iterable[tuple[date, date, str, str]], digests: Mapping[tuple[str, str], str]) -> list[Run]:
    """The runs of *segments*, ScheduleJoin.in_force's (first, last, version
    name, content) in date order, each with its calendar's digest from
    *digests* by (name, content). Adjacent segments of one timetable
    (_one_timetable) are one run, which keeps the name with a kept calendar."""
    out: list[Run] = []
    for first, last, stem, content in segments:
        calendar = digests.get((stem, content))
        prev = out[-1] if out else None
        if (
            prev is not None
            and prev.last + timedelta(days=1) == first
            and _one_timetable(prev.content, prev.calendar, content, calendar)
        ):
            out[-1] = prev._replace(last=last)
            if prev.calendar is None and calendar is not None:
                out[-1] = out[-1]._replace(stem=stem, calendar=calendar)
        else:
            out.append(Run(first, last, stem, content, calendar))
    return out


class Revision(NamedTuple):
    """The timetable in force before a revision and the one from its first day."""

    before: Run
    after: Run

    @property
    def first_day(self) -> date:
        return self.after.first


def revisions(in_order: Sequence[Run]) -> list[Revision]:
    """Each change of timetable between consecutive runs, oldest first. A run
    whose calendar is not kept reads the calendar last kept for its content,
    carried forward, so one apart from its neighbours by days no version is
    in force on neither hides a change of calendar around it nor makes one."""
    carried: list[Run] = []
    kept: dict[str, tuple[str, str]] = {}
    for run in in_order:
        if run.calendar is not None:
            kept[run.content] = (run.stem, run.calendar)
        elif run.content in kept:
            stem, calendar = kept[run.content]
            run = run._replace(stem=stem, calendar=calendar)
        carried.append(run)
    return [
        Revision(before, after)
        for before, after in pairwise(carried)
        if not _one_timetable(before.content, before.calendar, after.content, after.calendar)
    ]


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
    *dates* come nearest the revision first. An empty set is a set like any
    other: a type whose dates mostly run nothing plans no departure."""
    if not dates:
        return None
    running = [(day, calendar.running(day)) for day in dates]
    counts = Counter(services for _day, services in running)
    best = max(counts.values())
    return next(TypicalDay(day, services) for day, services in running if counts[services] == best)


def _quota(day_type: RevisionDayType, day: date) -> tuple[RevisionDayType, int | None]:
    return (day_type, day.weekday()) if day_type == "weekday" else (day_type, None)


def typical_days(run: Run, calendar: ServiceCalendar, *, latest: bool) -> dict[RevisionDayType, TypicalDay | None]:
    """*run*'s typical day of each day type, chosen among the dates of that
    type within *calendar*'s extent nearest the run's end when *latest* (the
    side before a revision), else nearest its start: up to SAMPLE_DATES of
    each weekday Monday to Friday for the weekday type, up to SAMPLE_DATES
    for each other type."""
    if latest:
        lo, hi = max(run.first, run.last - timedelta(days=_MAX_WALK_DAYS)), run.last
    else:
        lo, hi = run.first, min(run.last, run.first + timedelta(days=_MAX_WALK_DAYS))
    holidays = frozenset(public_holidays_between(lo, hi))
    left: dict[tuple[RevisionDayType, int | None], int] = {("weekday", weekday): SAMPLE_DATES for weekday in _WEEKDAYS}
    left |= {("saturday", None): SAMPLE_DATES, ("sunday_holiday", None): SAMPLE_DATES}
    picked: dict[RevisionDayType, list[date]] = {day_type: [] for day_type in DAY_TYPES}
    step = timedelta(days=-1 if latest else 1)
    day = hi if latest else lo
    while lo <= day <= hi and any(left.values()):
        # The weekday rule names only the three types this module reads.
        day_type = cast(RevisionDayType, fallback_day_type(day, holidays))
        quota = _quota(day_type, day)
        if left[quota] and calendar.covers(day):
            picked[day_type].append(day)
            left[quota] -= 1
        day += step
    return {day_type: typical_day(dates, calendar) for day_type, dates in picked.items()}


class Cell(NamedTuple):
    """One stop's departures in one hour of a typical day, with the earliest
    and latest of them in seconds after the service day's midnight."""

    departures: int
    first_sec: int
    last_sec: int


# day type -> stop_id -> hour -> Cell
Counts = Mapping[RevisionDayType, Mapping[str, Mapping[int, Cell]]]


class HourChange(NamedTuple):
    hour: int
    before: int
    after: int


class StopChange(NamedTuple):
    """One stop's departures on one day type's typical day before and after a
    revision. *lost* and *gained* add up each hour's fall and rise apart, so a
    departure moved to another hour is one lost and one gained. *hours* holds
    only the hours whose count changed."""

    stop_id: str
    day_type: RevisionDayType
    before: int
    after: int
    lost: int
    gained: int
    first_before: int | None
    first_after: int | None
    last_before: int | None
    last_after: int | None
    hours: tuple[HourChange, ...]

    @property
    def last_shift_min(self) -> int | None:
        """How many minutes later the last departure leaves, negative when
        earlier; None unless the stop has departures on both sides."""
        if self.last_before is None or self.last_after is None:
            return None
        return round((self.last_after - self.last_before) / 60)

    @property
    def ended(self) -> bool:
        return self.before > 0 and self.after == 0


def _bounds(cells: Mapping[int, Cell]) -> tuple[int | None, int | None]:
    return (
        min((cell.first_sec for cell in cells.values()), default=None),
        max((cell.last_sec for cell in cells.values()), default=None),
    )


def stop_changes(before: Counts, after: Counts, day_types: Iterable[RevisionDayType]) -> list[StopChange]:
    """Every stop of *day_types* whose departures changed: an hour's count, or
    its first or last departure. *day_types* are those both sides know."""
    changes: list[StopChange] = []
    for day_type in day_types:
        before_stops, after_stops = before.get(day_type, {}), after.get(day_type, {})
        for stop_id in sorted(before_stops.keys() | after_stops.keys()):
            was, now = before_stops.get(stop_id, {}), after_stops.get(stop_id, {})
            hours = [
                HourChange(hour, was[hour].departures if hour in was else 0, now[hour].departures if hour in now else 0)
                for hour in sorted(was.keys() | now.keys())
            ]
            changed = tuple(h for h in hours if h.before != h.after)
            first_before, last_before = _bounds(was)
            first_after, last_after = _bounds(now)
            if not changed and (first_before, last_before) == (first_after, last_after):
                continue
            changes.append(
                StopChange(
                    stop_id,
                    day_type,
                    sum(h.before for h in hours),
                    sum(h.after for h in hours),
                    sum(max(0, h.before - h.after) for h in changed),
                    sum(max(0, h.after - h.before) for h in changed),
                    first_before,
                    first_after,
                    last_before,
                    last_after,
                    changed,
                )
            )
    return changes


class DayTypeSummary(NamedTuple):
    """One day type's totals over every stop. A side whose typical day is
    unknown has no departure total; the stop counts need both sides."""

    day_type: RevisionDayType
    before: TypicalDay | None
    after: TypicalDay | None
    departures_before: int | None
    departures_after: int | None
    stops_ended: int | None
    last_earlier: int | None


def _departures(counts: Counts, day_type: RevisionDayType) -> int:
    return sum(cell.departures for hours in counts.get(day_type, {}).values() for cell in hours.values())


class Comparison(NamedTuple):
    revision: Revision
    day_types: tuple[DayTypeSummary, ...]
    stops: tuple[StopChange, ...]


def compare(
    revision: Revision,
    before_days: Mapping[RevisionDayType, TypicalDay | None],
    after_days: Mapping[RevisionDayType, TypicalDay | None],
    before: Counts,
    after: Counts,
) -> Comparison:
    """The revision's comparison: each day type's totals, and every changed
    stop of the day types both sides know, those that lost the most first."""
    known = {dt for dt in DAY_TYPES if before_days.get(dt) is not None and after_days.get(dt) is not None}
    stops = stop_changes(before, after, [dt for dt in DAY_TYPES if dt in known])
    summaries = []
    for day_type in DAY_TYPES:
        b, a = before_days.get(day_type), after_days.get(day_type)
        mine = [s for s in stops if s.day_type == day_type]
        summaries.append(
            DayTypeSummary(
                day_type,
                b,
                a,
                None if b is None else _departures(before, day_type),
                None if a is None else _departures(after, day_type),
                sum(s.ended for s in mine) if day_type in known else None,
                sum((s.last_shift_min or 0) < 0 for s in mine) if day_type in known else None,
            )
        )
    stops.sort(key=lambda s: (-s.lost, s.after - s.before, DAY_TYPES.index(s.day_type), s.stop_id))
    return Comparison(revision, tuple(summaries), tuple(stops))


# A trip's last stop is where it arrives, so it departs nowhere; an untimed
# stop time departs at no known hour. {services} holds each day type's
# running services, in DAY_TYPES order.
_DEPARTURES_SQL = f"""
    SELECT dt, stop_id, groupArray((hour, departures, first_sec, last_sec))
    FROM (
        SELECT dt, stop_id, intDiv(sec, 3600) AS hour, toInt64(count()) AS departures,
               min(sec) AS first_sec, max(sec) AS last_sec
        FROM (
            SELECT st.stop_id AS stop_id, assumeNotNull(s.service_type) AS service,
                   assumeNotNull(s.scheduled_sec) AS sec
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
        ARRAY JOIN arrayFilter(
            i -> has({{services:Array(Array(String))}}[i], service), range(1, {len(DAY_TYPES) + 1})
        ) AS dt
        GROUP BY dt, stop_id, hour
    )
    GROUP BY dt, stop_id
"""


def counts_of(rows: Iterable[tuple]) -> dict[RevisionDayType, dict[str, dict[int, Cell]]]:
    """_DEPARTURES_SQL's rows as Counts: one typical day's departures per
    day type, stop and hour, the stop from the kept stop times and the service
    and departure second from the kept schedule. A row holds one day type and
    stop with its hours in an array, so the result stays a few times the stop
    count, within the API client's max_result_rows."""
    out: dict[RevisionDayType, dict[str, dict[int, Cell]]] = {day_type: {} for day_type in DAY_TYPES}
    for index, stop_id, hours in rows:
        out[DAY_TYPES[index - 1]][stop_id] = {
            int(hour): Cell(int(departures), int(first), int(last)) for hour, departures, first, last in hours
        }
    return out


async def _counts(ch, agency_id: int, content: str, days: Mapping[RevisionDayType, TypicalDay | None]) -> Counts:
    services = [sorted(day.services) if (day := days.get(day_type)) is not None else [] for day_type in DAY_TYPES]
    if not any(services):
        return counts_of([])
    result = await ch.query(_DEPARTURES_SQL, parameters={"agency": agency_id, "content": content, "services": services})
    return counts_of(result.result_rows)


@async_lru_cache(maxsize=64, ttl_seconds=300, agency_arg="agency_id")
async def load_revisions(ch, agency_id: int) -> list[Revision]:
    """The agency's revisions, oldest first, among the versions in force whose
    schedule the archive keeps. Cached like the reports, and dropped when the
    agency's analyze rebuilds, which is when a new version is kept."""
    join = await static_archive.schedule_join_async(ch, agency_id, None, by_service_date=True)
    digests = await static_archive.calendar_digests_async(ch, agency_id)
    return revisions(runs(join.in_force(), digests))


class Unavailable(str, Enum):
    """Why an agency has no comparison to read."""

    NO_REVISION = "no_revision"
    NO_CALENDAR = "no_calendar"


async def _calendar(ch, agency_id: int, run: Run) -> ServiceCalendar | None:
    if run.calendar is None:
        return None
    kept = await static_archive.archived_calendar_async(ch, agency_id, run.stem, run.content)
    return None if kept is None else service_calendar(kept)


class Sides(NamedTuple):
    """Each side's typical day of each day type (typical_days)."""

    before: dict[RevisionDayType, TypicalDay | None]
    after: dict[RevisionDayType, TypicalDay | None]


async def load_sides(ch, agency_id: int, revision: Revision) -> Sides | Unavailable:
    """*revision*'s typical days, or NO_CALENDAR when either side's calendar
    is not kept: without it no day can be told to run anything, and reading
    its departures as none would report every stop cut. Called within a
    cached read, it keeps that read's NO_CALENDAR only briefly when a digest
    names a calendar the archive did not return, which a later read may find."""
    before_calendar, after_calendar = await asyncio.gather(
        _calendar(ch, agency_id, revision.before), _calendar(ch, agency_id, revision.after)
    )
    if before_calendar is None or after_calendar is None:
        if (revision.before.calendar is not None and before_calendar is None) or (
            revision.after.calendar is not None and after_calendar is None
        ):
            cache_briefly()
        return Unavailable.NO_CALENDAR
    return Sides(
        typical_days(revision.before, before_calendar, latest=True),
        typical_days(revision.after, after_calendar, latest=False),
    )


# A revision names both contents and both calendar digests, which together fix
# its comparison, so an entry never goes stale through a rebuild.
@async_lru_cache(maxsize=16, ttl_seconds=24 * 3600, agency_arg="agency_id", rebuild="never")
async def load_comparison(ch, agency_id: int, revision: Revision) -> Comparison | Unavailable:
    """*revision*'s comparison, or why it has none (load_sides). The result is
    shared by every caller and must not be modified."""
    sides = await load_sides(ch, agency_id, revision)
    if isinstance(sides, Unavailable):
        return sides
    before, after = await asyncio.gather(
        _counts(ch, agency_id, revision.before.content, sides.before),
        _counts(ch, agency_id, revision.after.content, sides.after),
    )
    return compare(revision, sides.before, sides.after, before, after)
