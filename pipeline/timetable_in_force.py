"""Which timetable is in force on a service date.

A version is a loaded timetable's name and the days it covers, its span: the
feed_start_date and feed_end_date of its feed_info.txt where they are given,
the span of its calendar (each calendar.txt range and each calendar_dates.txt
day) otherwise. On a service date the newest version whose span covers it is
in force. When the newest does not cover the date, because it starts later or
has ended, the newest of the others that does is in force. Newest is the later
fetch day the name carries, then the later first day, then the later name; a
name with no fetch day is older than any name with one. A date no version
covers has none in force.

Ingest joins each poll to the timetable loaded when it arrives, so a timetable
loaded ahead of its first day is joined by the rest of the day before, and one
loaded after its first day is not joined by the days it already covered.
analyze files each row under the version in force on its service date instead
(pipeline.static_archive), and a row of a date with none in force keeps the
timetable it was ingested against.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from datetime import date, datetime, timedelta
from itertools import pairwise
from typing import NamedTuple

from pipeline.strategies._time import parse_departure_time

# The fetchers name each zip they persist gtfs_static_YYYYMMDD after the day
# they fetched it, and the loader stamps that stem on every trip as the
# timetable's version name.
_FETCH_DAY = re.compile(r"(\d{4})(\d{2})(\d{2})$")

# The days a span may name: ClickHouse's Date32 range, which the archive keeps
# spans in. A feed naming a day outside it (an open end written as 99991231)
# covers up to the bound.
_FIRST_DAY = date(1900, 1, 1)
_LAST_DAY = date(2299, 12, 31)


def fetch_day(stem: str | None) -> date | None:
    """The day version name *stem* says its zip was fetched, or None for a name that carries none."""
    match = _FETCH_DAY.search(stem or "")
    if match is None:
        return None
    try:
        return date(int(match[1]), int(match[2]), int(match[3]))
    except ValueError:
        return None


class Version(NamedTuple):
    stem: str
    first: date
    last: date


class Segment(NamedTuple):
    """Consecutive service dates, both ends inclusive, with one version in force."""

    first: date
    last: date
    stem: str


def _newness(version: Version) -> tuple[date, date, str]:
    return (fetch_day(version.stem) or date.min, version.first, version.stem)


def in_force(versions: Iterable[Version]) -> list[Segment]:
    """The runs of service dates each version is in force over, in date order.

    A date no version covers is in no segment. A version whose span ends before
    it starts covers nothing.
    """
    spans = [version for version in versions if version.first <= version.last]
    edges = sorted({v.first for v in spans} | {v.last + timedelta(days=1) for v in spans})
    segments: list[Segment] = []
    for start, stop in pairwise(edges):
        last = stop - timedelta(days=1)
        covering = [v for v in spans if v.first <= start and last <= v.last]
        if not covering:
            continue
        stem = max(covering, key=_newness).stem
        if segments and segments[-1].stem == stem and segments[-1].last + timedelta(days=1) == start:
            segments[-1] = segments[-1]._replace(last=last)
        else:
            segments.append(Segment(start, last, stem))
    return segments


def gtfs_date(text: str | None) -> date | None:
    """A GTFS YYYYMMDD date, or None for text that is not eight ASCII digits naming a real day."""
    text = (text or "").strip()
    if not re.fullmatch(r"[0-9]{8}", text):
        return None
    try:
        return datetime.strptime(text, "%Y%m%d").date()
    except ValueError:
        return None


def version_span(
    feed_start: str | None, feed_end: str | None, covered: Iterable[tuple[date, date]]
) -> tuple[date, date] | None:
    """A timetable's span: *feed_start* and *feed_end*, feed_info.txt's raw
    YYYYMMDD text, where readable and in order; otherwise, end by end, the
    earliest first and latest last day of *covered*, the ranges its calendar
    covers (pipeline.static_loader). None when no span can be told."""
    ranges = [(first, last) for first, last in covered if first <= last]
    first, last = gtfs_date(feed_start), gtfs_date(feed_end)
    if first is not None and last is not None and first > last:
        first = last = None
    if first is None and ranges:
        first = min(start for start, _ in ranges)
    if last is None and ranges:
        last = max(end for _, end in ranges)
    if first is None or last is None or first > last:
        return None
    return max(first, _FIRST_DAY), min(last, _LAST_DAY)


def schedule_row(
    trip_id: str, stop_sequence: int, service_id: str | None, route_id: str | None, departure_time: str | None
) -> tuple[str, int, str | None, str | None, str | None, int | None]:
    """A stop time as static_join stamps a poll that matches it: the trip's
    service and route, and the departure parsed as ingest parses it."""
    scheduled_time, _status, scheduled_sec = parse_departure_time(departure_time)
    return (trip_id, stop_sequence, service_id, route_id, scheduled_time, scheduled_sec)
