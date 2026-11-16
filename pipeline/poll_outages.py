"""Poll success and outage minutes of one agency's month, from the instants its
polls were captured and the service hours of the days they count.

docs/methodology.md ("Feed quality") defines each figure; this module is its
arithmetic. A poll the feed answered with no entity stores no row, so a gap
between polls can be a feed with nothing to say as well as one that failed.
Only the part of a gap inside the agency's planned service counts, where an
empty feed would itself be a fault. Instants are POSIX seconds.
"""

from __future__ import annotations

import bisect
import statistics
from collections.abc import Collection, Iterable, Mapping, Sequence
from datetime import date, timedelta
from itertools import pairwise
from typing import Any, NamedTuple

from pipeline import static_archive
from pipeline.service_day import service_date_of, service_day_moment_utc, service_day_start_utc
from pipeline.service_hours import TripEnds, hours_of

#: A gap shorter than this is never an outage, however fast the feed is polled:
#: a few missed polls in a row are a slow vendor response or a fetch retry, not
#: an outage anyone acts on.
OUTAGE_FLOOR_SEC = 300

#: A gap must also exceed this many intervals, so a feed polled slowly is not
#: called out for missing one or two polls.
OUTAGE_INTERVALS = 3

#: How far beyond an outage's own gap another agency's poll is looked for to
#: bound a stretch no agency was polled in: longer than any agency's interval,
#: so the polls either side of a collector outage are found.
PEER_REACH_SEC = OUTAGE_FLOOR_SEC

#: The figures, each None beside ``polls_missing`` or ``outage_missing``.
FIGURES: tuple[str, ...] = (
    "in_service_min",
    "poll_interval_sec",
    "polls_in_service",
    "polls_expected",
    "poll_success_pct",
    "feed_outage_min",
    "feed_outage_longest_min",
    "collector_outage_min",
    "collector_outage_longest_min",
)

COLUMNS: tuple[str, ...] = ("poll_days", *FIGURES, "polls_missing", "outage_missing", "peer_archive_rows")


# Each trip of a kept content with its service and the times of its first
# and last stop times, as service_hours.TripEnds takes them. A tuple is never
# NULL, so argMin keeps the stop's row even where a time is missing.
TRIP_ENDS_CH_SQL = f"""
    SELECT st.trip_id, sc.service_id, st.first.1, st.first.2, st.last.1, st.last.2
    FROM (
        SELECT trip_id, argMin(tuple(departure_time, arrival_time), stop_sequence) AS first,
               argMax(tuple(arrival_time, departure_time), stop_sequence) AS last
        FROM {static_archive.ARCHIVE}
        WHERE agency_id = {{agency:Int32}} AND content_hash = {{content:String}}
        GROUP BY trip_id
    ) AS st
    INNER JOIN (
        SELECT trip_id, any(service_type) AS service_id
        FROM {static_archive.SCHEDULE}
        WHERE agency_id = {{agency:Int32}} AND content_hash = {{content:String}}
        GROUP BY trip_id
    ) AS sc ON sc.trip_id = st.trip_id
"""


def in_force_hours(conn, ch, agency_id: int, first: date, last: date) -> dict[date, tuple[int, int] | None]:
    """The hours (:func:`pipeline.service_hours.hours_of`) of each day of
    [first, last] by the timetable in force on it
    (pipeline.timetable_in_force), read from the archive. A day with none in
    force, or whose timetable's calendar is not kept or does not cover it, is
    absent: its hours are unknown."""
    join = static_archive.schedule_join(ch, agency_id, None, by_service_date=True)
    hours: dict[date, tuple[int, int] | None] = {}
    trips: dict[str, list[TripEnds]] = {}
    for seg_first, seg_last, stem, content in join.in_force():
        lo, hi = max(seg_first, first), min(seg_last, last)
        if lo > hi:
            continue
        calendar = static_archive.archived_calendar(ch, agency_id, stem, content)
        if calendar is None:
            continue
        if content not in trips:
            rows = ch.query(TRIP_ENDS_CH_SQL, parameters={"agency": agency_id, "content": content}).result_rows
            trips[content] = [TripEnds(*row) for row in rows]
        hours.update(
            hours_of(
                conn,
                agency_id,
                lo,
                hi,
                trips=trips[content],
                calendar=calendar.calendar,
                calendar_dates=calendar.calendar_dates,
            )
        )
    return hours


class Outage(NamedTuple):
    """One outage: the time from one interval after a gap opened inside
    service to where it closed, ``start`` to ``end``, and the gap it is part
    of, from the poll before it (or the window's start) to the poll after it
    (or the window's end)."""

    gap_start: int
    gap_end: int
    start: int
    end: int


class PollFigures(NamedTuple):
    in_service_sec: int
    polls: int
    interval_sec: float | None
    outages: list[Outage]


def threshold_sec(interval_sec: float) -> float:
    """The length a gap inside service must exceed to be an outage."""
    return max(OUTAGE_FLOOR_SEC, OUTAGE_INTERVALS * interval_sec)


def counted_spans(
    hours: Mapping[date, tuple[int, int] | None],
    *,
    first: date,
    through: date,
    observed_from: date | None,
    live_days: Collection[date],
) -> tuple[list[tuple[int, int]], int]:
    """The in-service spans the figures count, one per day, and how many days
    of the range have known hours.

    The days run from *first*, or *observed_from*, the agency's first observed
    day, if later, through *through*. *hours* gives each day whose timetable in
    force is known its first departure and last arrival in service-day
    seconds, or None when nothing timed runs. A day of *live_days* holds the
    live path's polls, a sample of the collector's, so its gaps say nothing
    about the feed. Each span is kept within its own service day, so a trip
    timed past the next cutover never counts twice.
    """
    start = max(first, observed_from) if observed_from else first
    spans, known = [], 0
    day = start
    while day <= through:
        if day in hours:
            known += 1
            span = hours[day]
            if span is not None and day not in live_days:
                lo = max(service_day_moment_utc(day, span[0]), service_day_start_utc(day))
                hi = min(service_day_moment_utc(day, span[1]), service_day_start_utc(day + timedelta(days=1)))
                if lo < hi:
                    spans.append((int(lo.timestamp()), int(hi.timestamp())))
        day += timedelta(days=1)
    return spans, known


def live_days(archive: Iterable[int], live: Iterable[int]) -> set[date]:
    """The service days most of whose polls came through the live path; a tie
    reads live, as :func:`pipeline.clickhouse.majority_source` calls a day."""
    from pipeline.clickhouse import majority_source

    counts: dict[date, list[int]] = {}
    for index, instants in enumerate((archive, live)):
        for instant in instants:
            counts.setdefault(_service_date(instant), [0, 0])[index] += 1
    return {day for day, (a, b) in counts.items() if majority_source(a, b) == "live"}


def merged_polls(archive: Sequence[int], live: Sequence[int], live_days: Collection[date]) -> list[int]:
    """The sorted distinct instants of *archive* and of those of *live* on
    *live_days* (:func:`live_days`). A day held from the archive keeps only
    its archived polls: a pushed copy of one of them is stamped on arrival,
    seconds after the instant its name gives, so it would count twice."""
    return sorted({*archive, *(instant for instant in live if _service_date(instant) in live_days)})


def _service_date(instant: int) -> date:
    from datetime import datetime, timezone

    return service_date_of(datetime.fromtimestamp(instant, tz=timezone.utc))


def poll_figures(polls: Sequence[int], spans: Sequence[tuple[int, int]], window: tuple[int, int]) -> PollFigures:
    """The figures of sorted distinct poll instants *polls* over the disjoint
    in-service *spans*. A gap before the first poll or after the last runs
    from or to the edge of *window*."""
    in_service = sum(hi - lo for lo, hi in spans)
    counted, gaps = 0, []
    pieces: list[tuple[int, int, int, int]] = []
    for lo, hi in spans:
        first, last = bisect.bisect_left(polls, lo), bisect.bisect_right(polls, hi)
        inside = list(polls[first:last])
        counted += len(inside)
        gaps += [b - a for a, b in pairwise(inside)]
        before = polls[first - 1] if first > 0 else window[0]
        after = polls[last] if last < len(polls) else window[1]
        points = [lo, *inside, hi]
        # Only the first and last pieces end at a span's edge rather than at
        # a poll; a poll on the edge leaves an empty piece there.
        for i, (a, b) in enumerate(pairwise(points)):
            if b > a:
                pieces.append((a if i > 0 else before, b if i < len(points) - 2 else after, a, b))
    if not gaps:
        return PollFigures(in_service, counted, None, [])
    interval = float(statistics.median(gaps))
    over = threshold_sec(interval)
    outages = [
        Outage(gap_start, gap_end, round(a + interval), b) for gap_start, gap_end, a, b in pieces if b - a > over
    ]
    return PollFigures(in_service, counted, interval, outages)


def split(outages: Sequence[Outage], stretches: Mapping[int, Sequence[tuple[int, int]]]) -> tuple[int, int, int, int]:
    """``(feed, longest feed, collector, longest collector)`` seconds of
    *outages*. *stretches* gives, by an outage's index, the stretches inside
    its gap that no other agency was polled in: the collector's, not the
    feed's. The longest of each is per outage."""
    feed_total = feed_longest = collector_total = collector_longest = 0
    for index, outage in enumerate(outages):
        ours = sum(max(0, min(outage.end, hi) - max(outage.start, lo)) for lo, hi in stretches.get(index, ()))
        theirs = outage.end - outage.start - ours
        feed_total += theirs
        feed_longest = max(feed_longest, theirs)
        collector_total += ours
        collector_longest = max(collector_longest, ours)
    return feed_total, feed_longest, collector_total, collector_longest


def _minutes(seconds: float) -> float:
    return round(seconds / 60, 1)


def columns(
    *,
    total_rows: int,
    undated_polls: int,
    known_days: int,
    spans: Sequence[tuple[int, int]],
    all_live: bool,
    figures: PollFigures,
    split: tuple[int, int, int, int],
    peer_rows: int,
) -> dict[str, Any]:
    """The scorecard's poll columns. *spans* are the counted days' and
    *known_days* the days with known hours (:func:`counted_spans`);
    *all_live* whether every known day with service holds the live path's
    polls; *split* is :func:`split` of the figures' outages, and *peer_rows*
    the other listed agencies' archived rows in the window, without which an
    outage cannot be told apart from the collector's."""
    missing: str | None = None
    if total_rows == 0:
        missing = "no_data"
    elif undated_polls:
        missing = "undated_polls"
    elif known_days == 0:
        missing = "no_service_hours"
    elif not spans:
        missing = "live_sampled" if all_live else "no_plan"
    elif figures.interval_sec is None:
        missing = "no_poll_interval"
    row: dict[str, Any] = dict.fromkeys(FIGURES)
    row.update(poll_days=len(spans), polls_missing=missing, outage_missing=missing, peer_archive_rows=peer_rows)
    if missing is not None or figures.interval_sec is None:
        return row
    expected = figures.in_service_sec / figures.interval_sec
    row.update(
        in_service_min=_minutes(figures.in_service_sec),
        poll_interval_sec=round(figures.interval_sec, 1),
        polls_in_service=figures.polls,
        polls_expected=round(expected),
        poll_success_pct=round(min(100.0, figures.polls / expected * 100), 1),
    )
    if figures.outages and peer_rows == 0:
        row["outage_missing"] = "no_peer"
        return row
    feed, feed_longest, collector, collector_longest = split
    row.update(
        feed_outage_min=_minutes(feed),
        feed_outage_longest_min=_minutes(feed_longest),
        collector_outage_min=_minutes(collector),
        collector_outage_longest_min=_minutes(collector_longest),
    )
    return row
