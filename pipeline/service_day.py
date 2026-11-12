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

A stop event, one trip leaving one stop, is filed under its trip's service
day rather than its readings' (:func:`filed_date_ch`): a feed predicts a stop
from before the cutover for a departure after it, and filing each reading by
its capture would count that departure on both days. A reading as such, for
the counts of what the feed sent, keeps its capture's service day.
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


# How near a neighbouring service day's scheduled departure a reading must be
# captured to be filed under that day: past a feed's longest prediction lead
# and the delay clamp, well short of the twelve hours that would make two days'
# departures equally near. At the cutover's length, a moved reading lies within
# this margin of its day's own span (:func:`filed_capture_span`).
FILING_REACH_HOURS = SERVICE_DAY_CUTOVER_HOURS


def scheduled_seconds_ch(prefix: str = "") -> str:
    """ClickHouse seconds from the service day's 00:00 to a stop's scheduled
    departure, over the `updates` or dedup columns named with *prefix*
    (``"u."``, ``"d."`` or ``""``).

    `scheduled_sec` where ingest stored one: only it holds a GTFS extended
    hour (``"24:10:00"`` is 87000), for which `scheduled_time` is NULL.
    Otherwise the `scheduled_time` string's hours, minutes and optional
    seconds, which is all the aomori_regex strategy's ``"HH:MM"`` rows, its
    trip's first departure, and the rows ingested before `scheduled_sec`
    existed carry. NULL when neither holds a time; a malformed string
    degrades to NULL rather than failing the query. `if`, not `ifNull`: `if`
    evaluates only the branch a row takes, so a row carrying `scheduled_sec`
    never pays for the string parse.
    """
    parts = f"splitByChar(':', ifNull({prefix}scheduled_time, ''))"
    from_text = f"toInt32OrNull({parts}[1]) * 3600 + toInt32OrNull({parts}[2]) * 60 + toInt32OrZero({parts}[3])"
    return f"if({prefix}scheduled_sec IS NOT NULL, {prefix}scheduled_sec, {from_text})"


def filed_date_ch(prefix: str = "u.") -> str:
    """The ClickHouse service date a reading of a stop event is filed under,
    over the `updates` columns named with *prefix*: its trip's service day.

    The reading's own service day (:func:`service_date_ch`), unless the stop's
    scheduled departure on the service day after it, or before it, lies within
    ``FILING_REACH_HOURS`` of the capture: then that day. The departure is
    :func:`scheduled_seconds_ch` placed on the service-day clock
    (:func:`service_day_offset_ch`), so a stop scheduled at 04:05 predicted at
    03:58 is the next service day's, and one scheduled at 27:56 (03:56 on the
    clock) reported at 04:03 the previous one's. A reading with no scheduled
    time, a trip the timetable did not match, keeps its own day.

    Only a capture within the reach of the cutover can move, so every other
    reading short-circuits to its own day. Reads only stored columns, so a
    read that refiles a row's schedule from the archive files it under the
    same day as the stored row (`pipeline.static_archive.observation_rows`).
    """
    captured = f"{prefix}captured_at"
    own = service_date_ch(captured)
    scheduled = service_day_offset_ch(scheduled_seconds_ch(prefix))
    midnight = f"toStartOfDay({captured} - INTERVAL {SERVICE_DAY_CUTOVER_HOURS} HOUR, 'Asia/Tokyo')"
    since_midnight = f"dateDiff('second', {midnight}, {captured})"
    reach = FILING_REACH_HOURS * 3600
    shift = (
        f"if(abs(({scheduled}) + 86400 - ({since_midnight})) <= {reach}, 1, "
        f"if(abs(({since_midnight}) + 86400 - ({scheduled})) <= {reach}, -1, 0))"
    )
    near_cutover = f"toHour({captured}, 'Asia/Tokyo') < {SERVICE_DAY_CUTOVER_HOURS + FILING_REACH_HOURS}"
    return f"if({near_cutover}, addDays({own}, {shift}), {own})"


def filed_between_ch(first: str, last: str, prefix: str = "u.") -> str:
    """A ClickHouse predicate keeping the readings :func:`filed_date_ch` files
    under the service dates *first* through *last*, two Date expressions such
    as bound parameters, over the `updates` columns named with *prefix*. It
    also ranges on the stored capture over :func:`filed_capture_span`, which
    the sort key prunes on."""
    start = f"toDateTime({first}, 'Asia/Tokyo') + INTERVAL {SERVICE_DAY_CUTOVER_HOURS - FILING_REACH_HOURS} HOUR"
    end = f"toDateTime({last}, 'Asia/Tokyo') + INTERVAL {24 + SERVICE_DAY_CUTOVER_HOURS + FILING_REACH_HOURS} HOUR"
    return (
        f"{prefix}captured_at >= {start} AND {prefix}captured_at < {end} "
        f"AND {filed_date_ch(prefix)} BETWEEN {first} AND {last}"
    )


def filed_date_of(captured_at: datetime, scheduled_seconds: int | None) -> date:
    """The service date :func:`filed_date_ch` files a reading under, for its
    capture instant and its stop's scheduled seconds as ingest stored them:
    the rule stated in Python, which the ClickHouse tests hold the SQL to."""
    own = service_date_of(captured_at)
    if scheduled_seconds is None or captured_at.astimezone(_JST).hour >= SERVICE_DAY_CUTOVER_HOURS + FILING_REACH_HOURS:
        return own
    scheduled = service_day_offset(scheduled_seconds)
    midnight = datetime.combine(own, time(0), tzinfo=_JST)
    since_midnight = int((captured_at - midnight).total_seconds())
    reach = FILING_REACH_HOURS * 3600
    if abs(scheduled + 86400 - since_midnight) <= reach:
        return own + timedelta(days=1)
    if abs(since_midnight + 86400 - scheduled) <= reach:
        return own - timedelta(days=1)
    return own


def filed_capture_span(first: date, last: date) -> tuple[datetime, datetime]:
    """The UTC capture instants, from the first up to the second, that hold
    every reading :func:`filed_date_ch` files under the service dates *first*
    through *last*: their own span widened by ``FILING_REACH_HOURS`` each side.
    A range on the stored ``captured_at`` prunes on the sort key, which a test
    of the filed date alone cannot."""
    reach = timedelta(hours=FILING_REACH_HOURS)
    return service_day_start_utc(first) - reach, service_day_start_utc(last + timedelta(days=1)) + reach


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
