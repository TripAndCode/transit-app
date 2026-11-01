"""The service day: the calendar day an observation is filed under.

A trip that runs from 23:40 to 00:30 belongs to one service day, not two, and a
00:15 departure on a Friday night is Friday's service. Filing observations by
their capture date splits such a trip across two dates, so the post-midnight
stops are counted twice (once with the stale pre-midnight estimate) and land
under the wrong date and weekday.

The service day therefore turns over at ``SERVICE_DAY_CUTOVER_HOURS`` in Japan
time rather than at midnight: an observation captured before that hour belongs
to the previous calendar day. The hour sits between the last after-midnight
departure and the first morning one, so every trip falls inside one service
day. Every query that keys, filters or buckets by the date of an observation
uses this one definition so the dedup, the aggregates and the range filters
cannot disagree at the edge of a day.
"""

from __future__ import annotations

import re
from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

_JST = ZoneInfo("Asia/Tokyo")

SERVICE_DAY_CUTOVER_HOURS = 4


def service_date_ch(column: str = "captured_at") -> str:
    """The ClickHouse expression for the service date of ``column``.

    Shifted back by the cutover before the Asia/Tokyo day is taken. Never a bare
    ``toDate(column)``: the stored instant is UTC, and a UTC day is the wrong
    civil day for every row captured between 00:00 and 09:00 in Japan.
    """
    return f"toDate({column} - INTERVAL {SERVICE_DAY_CUTOVER_HOURS} HOUR, 'Asia/Tokyo')"


def service_date_of(moment: datetime) -> date:
    """The service date a timestamp belongs to, matching :func:`service_date_ch`."""
    return (moment.astimezone(_JST) - timedelta(hours=SERVICE_DAY_CUTOVER_HOURS)).date()


def service_day_start_utc(day: date) -> datetime:
    """The UTC instant service day ``day`` begins: the cutover hour of that date in Japan time.

    Service day ``day`` runs from here up to the next day's start. A range on
    the stored ``captured_at`` between the two selects the rows
    :func:`service_date_ch` files under ``day``, and lets the sort key prune
    the scan.
    """
    return datetime.combine(day, time(SERVICE_DAY_CUTOVER_HOURS), tzinfo=_JST).astimezone(timezone.utc)


def service_day_seconds(hour: int, minute: int, second: int = 0) -> int:
    """Seconds since the start of the service day for a wall-clock time.

    A clock before the cutover hour is an after-midnight stop of the service
    day that began the evening before, so it is a day later than its face
    value: ``00:10`` is ``24:10``. GTFS's own extended hours (``24:10``) already
    say so and are left as they are.
    """
    return service_day_offset(hour * 3600 + minute * 60 + second)


def service_day_offset(seconds: int) -> int:
    """:func:`service_day_seconds` for a value already in seconds since 00:00,
    such as the ``scheduled_sec`` ingest stores from a feed's clock as
    written: below the cutover it is a day later, and a GTFS extended hour
    (``24:10`` as 87000) is left as it is."""
    return seconds + 24 * 3600 if seconds < SERVICE_DAY_CUTOVER_HOURS * 3600 else seconds


def service_day_offset_ch(seconds: str) -> str:
    """The ClickHouse mirror of :func:`service_day_offset`, over an integer
    expression counting seconds from midnight, such as ``scheduled_sec``. A
    NULL stays NULL: ClickHouse's ``if`` takes a NULL condition as false."""
    return f"if(({seconds}) < {SERVICE_DAY_CUTOVER_HOURS * 3600}, ({seconds}) + 86400, ({seconds}))"


_CLOCK_RE = re.compile(r"^(\d{1,2}):([0-5]\d)(?::([0-5]\d))?$")


def clock_to_sec(text: str | None) -> int | None:
    """Seconds since the service day's 00:00 for a GTFS ``HH:MM[:SS]`` clock,
    or None for anything that is not one.

    Fallback for rows whose ``scheduled_sec`` is NULL: the `aomori_regex`
    ingest strategy writes a 5-char ``"HH:MM"`` with no seconds, while
    static_join agencies write 8 chars. An hour past 24 is a real GTFS
    post-midnight continuation and stays unwrapped, and a clock before the
    service-day cutover (``00:10``) is placed a day later
    (:func:`service_day_seconds`), so a trip that crosses midnight keeps a
    monotonically increasing position on a time axis.
    """
    if not text:
        return None
    m = _CLOCK_RE.match(text)
    if m is None:
        return None
    return service_day_seconds(int(m.group(1)), int(m.group(2)), int(m.group(3) or 0))


def service_day_seconds_sql(seconds: str) -> str:
    """The Postgres mirror of :func:`service_day_seconds`, over an integer
    expression already counting seconds from midnight
    (:func:`pipeline.db.hms_to_sec_sql`). A NULL stays NULL.
    """
    return f"(CASE WHEN ({seconds}) < {SERVICE_DAY_CUTOVER_HOURS * 3600} THEN ({seconds}) + 86400 ELSE ({seconds}) END)"


def service_day_moment_utc(day: date, seconds: int) -> datetime:
    """The UTC instant a timetable time of service day ``day`` falls at.

    A GTFS time counts from midnight of its service date in the feed's zone,
    Japan time here, which has no daylight saving, so ``24:27`` is 00:27 the
    next morning.
    """
    return (datetime.combine(day, time(0), tzinfo=_JST) + timedelta(seconds=seconds)).astimezone(timezone.utc)
