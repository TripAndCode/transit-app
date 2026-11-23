"""Hold the feed's kept TripUpdates prediction against stop passages observed
in archived VehiclePositions, for one agency over a range of service dates.

Every delay the app reports is the operator's prediction, the latest timely
reading the feed sent for a stop event (``pipeline.db.build_dedup_ch_sql``),
not an observation. This measures how far that kept value is from when the
bus actually left, offline, from the collector's R2 archives alone: no
database is read or written. It reads TripUpdates as the static_join
strategy does (``pipeline.strategies.static_join.decode_trip_updates``), the
strategy of the Hiroshima feeds (agencies 8 to 10) the collector also polls
VehiclePositions for.

**Inputs** sit under ``--archives`` as R2 lays them out:
``rt/<id>/<YYYYMMDD>.tar.gz`` (``YYYYMMDD/TripUpdate_HHMMSS.pb``),
``vp/<id>/<YYYYMMDD>.tar.gz`` (``YYYYMMDD/VehiclePosition_HHMMSS.pb``), each
the polls of one UTC day named in UTC, and
``static/<id>/gtfs_static_<YYYYMMDD>.zip``. Every capture filed under service
date D is in the TripUpdates archives of the UTC days D-1 to D+1 and the
VehiclePositions archives of D-1 and D (``_archive_days``). Each TripUpdates
archive is read once; each poll is joined to the newest timetable dated on
or before its capture's service day, as ingest joins it to the one loaded
then, and the stops of D are placed with the timetable for D. ``--fetch`` first downloads
whichever of those R2 holds and the mirror lacks.

**The kept reading** of a stop event, keyed by trip, filed service date and
stop_sequence, is the one the app keeps, and not the last reading before the
passage: the validation is of the values the product reports. Of the
readings that are timely (``pipeline.db.timely_reading``) and either carry a
delay inside the plausibility clamp or withdraw the departure (trip CANCELED,
stop SKIPPED), it is the latest by capture instant then file name. A trip
CANCELED without listing a stop withdraws each stop still to come, as
ingest's ``parse_feed`` stores it (``cancellation_withdrawals``); a stop
already left keeps its reading. A stop event whose kept reading withdraws
its departure has no departure to measure. The capture instant is the one
archive ingest stamps (``archive_captured_at``), so the pick matches the app
on days it ingested from the archive. On a day the app holds the collector's
live pushes instead, its capture instants are the collector's fetch times,
and which days those are is not in the archives.

**The observed passage** comes from the trip's vehicle track: its positions
for that trip and service date, resent fixes and GPS jumps dropped. Each
visit to a stop, a run of track segments within PASSAGE_RADIUS_M of it, is a
candidate passage. Of the segments nearest the stop (DWELL_TOLERANCE_M), the
last gives the departure and, at the trip's final stop, the first gives the
arrival, so a bus idling at its first stop departs when it pulls out and one
laying over at its last arrives when it gets there. The instant is
interpolated on that segment, linearly along it, except where a fix shows
the bus standing at the stop: then the bus is taken to cover the stretch
from the stop to its next fix at the speed it is next seen moving (to its
arrival, from its previous fix at the speed it was last seen moving), which
places a departure from a dwell between two polls rather than at the last
fix at the stop. No segment spanning more than MAX_BRACKET_GAP_SEC is
interpolated. A visit whose bus is last seen standing at the stop gives no
departure, one that ends still short of the final stop gives no arrival,
and a candidate more than MAX_PLAUSIBLE_DELAY_SEC off schedule is dropped.
The stops are then aligned with the track as a whole (``passages_explained``):
at most one candidate per stop, instants increasing along stop_sequence,
the delay moving by at most MAX_DELAY_CHANGE_SEC, plus DELAY_DRIFT_SHARE of
the scheduled time, between matched stops, as
many stops as possible, ties broken towards the steadiest delay. A loop's
return to its first stop, a deadhead past the stops, or the road out past a
stop served on the way back each lose, since taking them forfeits the stops
between. A stop with no passage is counted by why: the track crossed it,
between the stops either side, only across a gap too long to time; no visit
at all; no visit showing the bus leave (arrive); only implausible ones; one
only the delay bound kept out; or none the alignment could take in order.

**The comparison** per stop event with both: error = kept delay - observed
delay, positive when the feed reported the bus later than it left; lead =
passage - capture of the kept reading, negative when that reading came after
the bus had left (counted as ``kept_after_passage``). The summary gives MAE,
bias and the 90th percentile of the absolute error overall, by lead
(LEAD_WIDTH_SEC groups, open beyond ±LEAD_CAP_SEC), by service-day hour of
the scheduled departure and by timepoint ("unknown" where stop_times.txt has
no timepoint column), plus coverage: how many kept stop events had a
passage, and why the rest did not (``no_passage_*``). It also classes each
measured stop event as early, on time or late under ON_TIME_WINDOW, once by
its kept delay and once by its observed one, which shows what the error
does to the on-time percentage the product reports.

Output: ``<out>/<id>/<FROM>_<TO>.json`` and a Markdown table beside it as
``.md``. The run exits 1 when no stop event could be compared.

Credentials (``--fetch`` only): the transit-archives pair in
AWS_ACCESS_KEY_ID / AWS_SECRET_ACCESS_KEY, with OBJECT_STORE_BUCKET and
OBJECT_STORE_ENDPOINT, as ``scripts/rt_validate_sample.py`` reads them. The
aws CLI's own output is captured and dropped; a failed download is reported
by its key and exit status.

Usage:
    python scripts/validate_vp_predictions.py --agency ID --from YYYY-MM-DD
        --to YYYY-MM-DD --archives DIR [--out DIR] [--fetch]
"""

from __future__ import annotations

import argparse
import csv
import io
import json
import math
import os
import re
import struct
import subprocess
import sys
import tarfile
import zipfile
from collections import Counter, defaultdict
from collections.abc import Callable, Iterable, Iterator, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from itertools import pairwise
from pathlib import Path, PurePosixPath
from typing import Any
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from ml.r2 import default_capture, download_file, list_keys  # noqa: E402  (sys.path injection above)
from pipeline.clickhouse import STOP_SKIPPED, TRIP_CANCELED  # noqa: E402
from pipeline.db import MAX_PLAUSIBLE_DELAY_SEC, timely_reading  # noqa: E402
from pipeline.reports.rankings import DEFAULT_PRESET, ON_TIME_PRESETS  # noqa: E402
from pipeline.service_day import (  # noqa: E402
    FILING_REACH_HOURS,
    SERVICE_DAY_CUTOVER_HOURS,
    filed_date_of,
    service_date_of,
    service_day_moment_utc,
    service_day_offset,
)
from pipeline.strategies._pb import _dec, _fields, _uint16, archive_captured_at, decode_feed_timestamp  # noqa: E402
from pipeline.strategies._time import parse_departure_time  # noqa: E402
from pipeline.strategies.static_join import (  # noqa: E402
    cancellation_withdrawals,
    decode_trip_updates,
    is_stopless_cancellation,
)

SCHEMA = 2
_JST = ZoneInfo("Asia/Tokyo")

# Mean Earth radius times pi/180. Distances are taken on a flat map centred
# on each stop, exact enough over the few hundred metres a passage spans.
METERS_PER_DEGREE = 6_371_000 * math.pi / 180

# How close the track must come to a stop to have passed it: a bus GPS fix
# strays by up to a few tens of metres in a street canyon, and a stop's
# coordinates can name the pole across the road or the stop group's centre.
# Wider risks taking a neighbouring stop's approach for this one, which the
# stop_sequence order then has to undo.
PASSAGE_RADIUS_M = 50.0
# Segments within this much of the nearest approach count as standing at the
# stop: the scatter of fixes from a bus that is not moving.
DWELL_TOLERANCE_M = 15.0
# The longest gap between two fixes a passage is interpolated across. In a
# longer one a bus can dwell, queue or detour between the two, so a straight
# line between them no longer times when it passed; a shorter limit leaves
# more stops untimed at the feeds' polling interval.
MAX_BRACKET_GAP_SEC = 150
# How far the observed delay may move from one matched stop to the next:
# MAX_DELAY_CHANGE_SEC, plus DELAY_DRIFT_SHARE of the scheduled time between
# them. A delay rarely moves by many minutes between neighbouring stops, but
# over a long stretch, such as the next lap of a circulator, a bus can fall
# behind by a share of the time it was scheduled to take; a figure-eight or a
# later lap past the same place is further off still. A passage itself must
# lie within MAX_PLAUSIBLE_DELAY_SEC of its schedule, the app's own clamp.
MAX_DELAY_CHANGE_SEC = 900
DELAY_DRIFT_SHARE = 0.5
# The grid that finds the segments near a stop: any cell larger than the
# radius works; the slack covers a flat map drawn from the track's first fix
# rather than centred on each stop.
_GRID_CELL_M = 250.0
_GRID_SLACK_M = 20.0
# Faster than any bus: a fix implying more is a GPS jump and is dropped.
MAX_SPEED_MPS = 40.0
# Below this a segment is a bus creeping or standing, too slow to time the
# stretch from a stop to the next fix by.
MIN_MOVING_SPEED_MPS = 1.0
# The same trip_id runs on every service day; fixes this far apart belong to
# runs on different days, while a single run never pauses this long.
TRACK_SPLIT_GAP_SEC = 3 * 3600

# Lead-time groups. A minute holds the last poll or two before a passage at
# the cadence the feeds are polled at; past ten minutes the groups are
# pooled, since few kept readings are that old.
LEAD_WIDTH_SEC = 60
LEAD_CAP_SEC = 600

# The on-time window every figure whose request names none counts by, the
# board's included: each measured stop event is classed by it twice, by its
# kept delay and by its observed one.
ON_TIME_WINDOW = ON_TIME_PRESETS[DEFAULT_PRESET]
ON_TIME_CLASSES = ("early", "on_time", "late")

_MEMBER = re.compile(r"^(?P<day>\d{8})/(?P<kind>TripUpdate|VehiclePosition)_(?P<clock>\d{6})\.pb$")
_STATIC_NAME = re.compile(r"^gtfs_static_(?P<day>\d{8})\.zip$")

Run = Callable[[Sequence[str]], None]
Capture = Callable[[Sequence[str]], str]
Poll = tuple[str, datetime, bytes]


@dataclass(frozen=True, slots=True)
class Fix:
    at: datetime
    lat: float
    lon: float


@dataclass(frozen=True, slots=True)
class StopPoint:
    """A stop of a trip to find the passage of. ``final`` marks the trip's
    last stop, whose passage is its arrival."""

    stop_sequence: int
    lat: float
    lon: float
    scheduled: datetime
    final: bool = False


@dataclass(frozen=True, slots=True)
class Sighting:
    trip_id: str
    vehicle_id: str
    start_date: str | None
    at: datetime
    lat: float
    lon: float


@dataclass(frozen=True, slots=True)
class StopTime:
    """A timetable stop time. ``departure_time`` is the raw GTFS text and
    ``scheduled_sec`` its seconds as ingest stores them, None where the
    timetable gives none; ``timepoint`` is None where the timetable has no
    timepoint column."""

    stop_sequence: int
    stop_id: str
    departure_time: str | None
    timepoint: bool | None
    scheduled_sec: int | None = field(init=False, compare=False)

    def __post_init__(self) -> None:
        object.__setattr__(self, "scheduled_sec", parse_departure_time(self.departure_time)[2])


@dataclass(frozen=True, slots=True)
class Timetable:
    stops: dict[str, tuple[float, float]]
    trips: dict[str, tuple[StopTime, ...]]
    seconds: dict[str, dict[int, int | None]] = field(init=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        seconds = {t: {st.stop_sequence: st.scheduled_sec for st in sts} for t, sts in self.trips.items()}
        object.__setattr__(self, "seconds", seconds)


@dataclass(frozen=True, slots=True)
class Reading:
    """A stop event's kept reading; ``dep_delay`` is None when it withdraws the departure."""

    captured_at: datetime
    file_name: str
    dep_delay: int | None


@dataclass(frozen=True, slots=True)
class Event:
    trip_id: str
    service_date: date
    stop_sequence: int
    scheduled_sec: int
    timepoint: bool | None
    predicted_delay_sec: int
    observed_delay_sec: float
    lead_sec: float

    @property
    def error_sec(self) -> float:
        return self.predicted_delay_sec - self.observed_delay_sec


@dataclass(frozen=True, slots=True)
class _Approach:
    segment: int
    fraction: float
    distance: float


def _xy(lat: float, lon: float, origin_lat: float, origin_lon: float) -> tuple[float, float]:
    return (
        (lon - origin_lon) * METERS_PER_DEGREE * math.cos(math.radians(origin_lat)),
        (lat - origin_lat) * METERS_PER_DEGREE,
    )


def _distance(a: Fix | StopPoint | Sighting, b: Fix | StopPoint | Sighting) -> float:
    return math.hypot(*_xy(a.lat, a.lon, b.lat, b.lon))


def _seconds(a: datetime, b: datetime) -> float:
    return (b - a).total_seconds()


def _usable(track: Sequence[Fix], j: int) -> bool:
    """Whether segment *j*, from fix j to fix j+1, can be interpolated on."""
    return 0 <= j < len(track) - 1 and 0 < _seconds(track[j].at, track[j + 1].at) <= MAX_BRACKET_GAP_SEC


def _speed(track: Sequence[Fix], j: int) -> float | None:
    if not _usable(track, j):
        return None
    speed = _distance(track[j], track[j + 1]) / _seconds(track[j].at, track[j + 1].at)
    return speed if speed >= MIN_MOVING_SPEED_MPS else None


def _approach(track: Sequence[Fix], j: int, stop: StopPoint, min_fraction: float) -> _Approach:
    a, b = track[j], track[j + 1]
    ax, ay = _xy(a.lat, a.lon, stop.lat, stop.lon)
    bx, by = _xy(b.lat, b.lon, stop.lat, stop.lon)
    dx, dy = bx - ax, by - ay
    length2 = dx * dx + dy * dy
    fraction = 0.0 if length2 == 0 else -(ax * dx + ay * dy) / length2
    fraction = min(1.0, max(min_fraction, fraction))
    return _Approach(j, fraction, math.hypot(ax + fraction * dx, ay + fraction * dy))


def _gap(track: Sequence[Fix], j: int) -> bool:
    """Whether segment *j* spans more than MAX_BRACKET_GAP_SEC, too long to
    time a passage on."""
    return _seconds(track[j].at, track[j + 1].at) > MAX_BRACKET_GAP_SEC


def _segments_near(track: Sequence[Fix], stops: Sequence[StopPoint]) -> list[list[int]]:
    """For each stop, the usable segments that could come within
    PASSAGE_RADIUS_M of it, in track order: those whose bounding box, widened
    by the radius, covers the stop's grid cell. A coarse grid keeps the search
    linear in the track and the stops rather than their product."""
    origin = track[0]
    points = [_xy(f.lat, f.lon, origin.lat, origin.lon) for f in track]
    pad = PASSAGE_RADIUS_M + _GRID_SLACK_M
    grid: dict[tuple[int, int], list[int]] = defaultdict(list)
    for j in range(len(track) - 1):
        if not _usable(track, j):
            continue
        (ax, ay), (bx, by) = points[j], points[j + 1]
        for cx in range(
            math.floor((min(ax, bx) - pad) / _GRID_CELL_M), math.floor((max(ax, bx) + pad) / _GRID_CELL_M) + 1
        ):
            for cy in range(
                math.floor((min(ay, by) - pad) / _GRID_CELL_M), math.floor((max(ay, by) + pad) / _GRID_CELL_M) + 1
            ):
                grid[(cx, cy)].append(j)
    near = []
    for stop in stops:
        x, y = _xy(stop.lat, stop.lon, origin.lat, origin.lon)
        near.append(grid.get((math.floor(x / _GRID_CELL_M), math.floor(y / _GRID_CELL_M)), []))
    return near


def _visits(track: Sequence[Fix], stop: StopPoint, segments: Sequence[int]) -> list[list[_Approach]]:
    """Every visit of *stop*: each run of consecutive usable segments within
    PASSAGE_RADIUS_M of it, in track order."""
    visits: list[list[_Approach]] = []
    for j in segments:
        approach = _approach(track, j, stop, 0.0)
        if approach.distance > PASSAGE_RADIUS_M:
            continue
        if visits and visits[-1][-1].segment == j - 1:
            visits[-1].append(approach)
        else:
            visits.append([approach])
    return visits


def _nearest(visit: Sequence[_Approach]) -> tuple[list[_Approach], float]:
    """The visit's segments standing at the stop, and the distance that counts as there."""
    there = min(a.distance for a in visit) + DWELL_TOLERANCE_M
    return [a for a in visit if a.distance <= there], there


def _offset(span: float, fraction: float, distance: float, speed: float | None, *, towards: bool) -> float:
    """Seconds into a segment *span* seconds long at which the bus is at the
    point *fraction* along it: linearly, or, given *speed*, timed back from
    the segment's end (``towards`` False) or on from its start (True)."""
    if speed is None:
        offset = fraction * span
    elif towards:
        offset = fraction * distance / speed
    else:
        offset = span - (1 - fraction) * distance / speed
    return min(span, max(0.0, offset))


def _departure(track: Sequence[Fix], visit: Sequence[_Approach], stop: StopPoint) -> tuple[_Approach, datetime] | None:
    nearest, there = _nearest(visit)
    last = nearest[-1]
    a, b = track[last.segment], track[last.segment + 1]
    if _distance(b, stop) <= there and not _usable(track, last.segment + 1):
        return None
    dwelt = _distance(a, stop) <= there
    speed = _speed(track, last.segment + 1) if dwelt else None
    offset = _offset(_seconds(a.at, b.at), last.fraction, _distance(a, b), speed, towards=False)
    return last, a.at + timedelta(seconds=offset)


def _arrival(track: Sequence[Fix], visit: Sequence[_Approach], stop: StopPoint) -> tuple[_Approach, datetime] | None:
    nearest, there = _nearest(visit)
    first = nearest[0]
    a, b = track[first.segment], track[first.segment + 1]
    if _distance(a, stop) <= there and not _usable(track, first.segment - 1):
        return None
    if first.fraction == 1.0 and first.distance > DWELL_TOLERANCE_M and not _usable(track, first.segment + 1):
        return None
    stood = _distance(b, stop) <= there
    speed = _speed(track, first.segment - 1) if stood else None
    offset = _offset(_seconds(a.at, b.at), first.fraction, _distance(a, b), speed, towards=True)
    return first, a.at + timedelta(seconds=offset)


@dataclass(frozen=True, slots=True)
class _Candidate:
    stop: int
    at: datetime
    segment: int
    fraction: float


def _candidates(
    track: Sequence[Fix], stops: Sequence[StopPoint]
) -> tuple[list[list[_Candidate]], list[list[int]], dict[int, str]]:
    """Each stop's candidate passages, one per visit the departure (arrival)
    rules time, within MAX_PLAUSIBLE_DELAY_SEC of its schedule; the gaps
    between fixes too long to time that pass within PASSAGE_RADIUS_M of it;
    and, by stop index, why a stop with no candidate has none by its own
    visits: ``no_visit``, ``unseen`` (no visit shows the bus leaving, or
    arriving) or ``implausible``.

    A gap crosses a stop when the straight line between its two fixes passes
    within PASSAGE_RADIUS_M. The road between them can curve away from that
    line, so a stop passed on a bend inside a gap is counted ``no_visit``
    rather than ``gap``: the count of ``gap`` is a lower bound.

    Gaps are few on a track, and one can span many kilometres, so each is
    tested against every stop rather than laid on the grid."""
    out: list[list[_Candidate]] = []
    crossings: list[list[int]] = []
    own: dict[int, str] = {}
    near = _segments_near(track, stops)
    gaps = [j for j in range(len(track) - 1) if _gap(track, j)]
    for i, stop in enumerate(stops):
        crossings.append([j for j in gaps if _approach(track, j, stop, 0.0).distance <= PASSAGE_RADIUS_M])
        visits = _visits(track, stop, near[i])
        timed = [p for v in visits if (p := (_arrival if stop.final else _departure)(track, v, stop)) is not None]
        plausible = [
            _Candidate(i, at, chosen.segment, chosen.fraction)
            for chosen, at in timed
            if abs(_seconds(stop.scheduled, at)) <= MAX_PLAUSIBLE_DELAY_SEC
        ]
        out.append(plausible)
        if timed:
            if not plausible:
                own[i] = "implausible"
        elif visits:
            own[i] = "unseen"
        else:
            own[i] = "no_visit"
    return out, crossings, own


def _after(
    track: Sequence[Fix], stop: StopPoint, c: _Candidate, prev: _Candidate, prev_at: datetime
) -> datetime | None:
    """The instant *c* gives *stop* when the stop before it in the chain was
    passed at *prev_at* by candidate *prev*, or None if it cannot follow. A
    stop mapped just behind the one before it on the same segment, still
    within the radius of where that one was passed, is passed with it."""
    if c.at > prev_at:
        return c.at
    if c.segment == prev.segment and _approach(track, c.segment, stop, prev.fraction).distance <= PASSAGE_RADIUS_M:
        return prev_at
    return None


def passages_explained(track: Sequence[Fix], stops: Sequence[StopPoint]) -> tuple[dict[int, datetime], dict[int, str]]:
    """The observed passage of each of *stops* (in stop_sequence order) that
    the time-ordered *track* shows, by stop_sequence, and why each other stop
    has none (see :func:`_no_passage_why`).

    At most one candidate is taken per stop, with instants increasing along
    stop_sequence and the delay moving by at most MAX_DELAY_CHANGE_SEC plus
    DELAY_DRIFT_SHARE of the scheduled time from one taken stop to the next.
    Of such sequences the longest is taken, ties broken by the smallest sum
    of the first delay's size and each change of delay along it: a loop's return to its first stop, a deadhead past the
    stops, or the road out past a stop served on the way back would each
    forfeit the stops between."""
    if len(track) < 2:
        return {}, {stop.stop_sequence: "no_visit" for stop in stops}
    candidates, crossings, own = _candidates(track, stops)
    flat = [c for per_stop in candidates for c in per_stop]
    # (stops taken, cost, previous index in flat or -1, this stop's instant)
    best: list[tuple[int, float, int, datetime]] = []
    for i, c in enumerate(flat):
        stop = stops[c.stop]
        choice = (1, abs(_seconds(stop.scheduled, c.at)), -1, c.at)
        for p in range(i):
            prev = flat[p]
            if prev.stop >= c.stop:
                continue
            length, cost, _before, prev_at = best[p]
            at = _after(track, stop, c, prev, prev_at)
            if at is None:
                continue
            change = abs(_seconds(stop.scheduled, at) - _seconds(stops[prev.stop].scheduled, prev_at))
            stretch = _seconds(stops[prev.stop].scheduled, stop.scheduled)
            if change > MAX_DELAY_CHANGE_SEC + DELAY_DRIFT_SHARE * abs(stretch):
                continue
            option = (length + 1, cost + change, p, at)
            if (option[0], -option[1]) > (choice[0], -choice[1]):
                choice = option
        best.append(choice)
    taken: dict[int, datetime] = {}
    end = max(range(len(flat)), key=lambda k: (best[k][0], -best[k][1]), default=-1)
    while end >= 0:
        taken[flat[end].stop] = best[end][3]
        end = best[end][2]
    why = {
        stop.stop_sequence: _no_passage_why(track, candidates[i], crossings[i], own.get(i), i, taken)
        for i, stop in enumerate(stops)
        if i not in taken
    }
    return {stops[i].stop_sequence: at for i, at in sorted(taken.items())}, why


def _no_passage_why(
    track: Sequence[Fix],
    candidates: Sequence[_Candidate],
    crossings: Sequence[int],
    own: str | None,
    i: int,
    taken: Mapping[int, datetime],
) -> str:
    """Why stop *i* has no passage, given *taken*, the instants of the stops
    the alignment took, by index, and what lies in time between the taken
    stops either side. ``gap`` when one of the stop's *crossings* does: the
    bus passed it there, unseen, so whatever its visits show is another
    pass. Otherwise *own*, the reason its visits give when it has no
    candidate (:func:`_candidates`); ``delay_jump`` when a candidate falls
    between, so only the delay bound kept it out: a longest sequence would
    otherwise have taken it; and ``out_of_order`` when every candidate falls
    before a taken stop it must follow or after one it must precede. A
    candidate's bounds are inclusive, since a stop can share the instant of
    the one before it (:func:`_after`). A gap's are strict: no passage is
    timed inside one, so a gap that only touches a neighbour's instant lies
    wholly before or after it."""
    before = max((at for k, at in taken.items() if k < i), default=None)
    after = min((at for k, at in taken.items() if k > i), default=None)

    if any((before is None or track[j + 1].at > before) and (after is None or track[j].at < after) for j in crossings):
        return "gap"
    if own is not None:
        return own
    if any((before is None or c.at >= before) and (after is None or c.at <= after) for c in candidates):
        return "delay_jump"
    return "out_of_order"


def passages(track: Sequence[Fix], stops: Sequence[StopPoint]) -> dict[int, datetime]:
    """:func:`passages_explained` without the reasons."""
    return passages_explained(track, stops)[0]


def _float32(value: Any) -> float | None:
    if not isinstance(value, int):
        return None
    result = struct.unpack("<f", struct.pack("<I", value & 0xFFFF_FFFF))[0]
    return result if math.isfinite(result) else None


def _instant(posix: Any) -> datetime | None:
    if not isinstance(posix, int) or posix <= 0:
        return None
    try:
        return datetime.fromtimestamp(posix, timezone.utc)
    except (OverflowError, OSError, ValueError):
        return None


def decode_vehicle_positions(pb_bytes: bytes, polled_at: datetime) -> list[Sighting]:
    """Each VehiclePosition of the feed that names a trip and a position. It
    is timed by its own timestamp, else the feed header's, else *polled_at*;
    its vehicle is the descriptor's id, else its label, else the entity id."""
    header_at = _instant(decode_feed_timestamp(pb_bytes)) or polled_at
    out = []
    for entity_bytes in _fields(pb_bytes).get(2, []):
        entity = _fields(entity_bytes) if isinstance(entity_bytes, bytes) else {}
        if not isinstance(entity.get(4, [None])[0], bytes):
            continue
        vp = _fields(entity[4][0])
        trip = _fields(vp[1][0]) if isinstance(vp.get(1, [None])[0], bytes) else {}
        trip_id = _dec(trip.get(1, [None])[0])
        position = _fields(vp[2][0]) if isinstance(vp.get(2, [None])[0], bytes) else {}
        lat, lon = _float32(position.get(1, [None])[0]), _float32(position.get(2, [None])[0])
        if not isinstance(trip_id, str) or not trip_id or lat is None or lon is None:
            continue
        if not (-90 <= lat <= 90 and -180 <= lon <= 180) or (lat == 0 and lon == 0):
            continue
        vehicle = _fields(vp[8][0]) if isinstance(vp.get(8, [None])[0], bytes) else {}
        names = (vehicle.get(1, [None])[0], vehicle.get(2, [None])[0], entity.get(1, [None])[0])
        vehicle_id = next((n for n in (_dec(x) for x in names) if isinstance(n, str) and n), "")
        start_date = _dec(trip.get(3, [None])[0])
        out.append(
            Sighting(
                trip_id,
                vehicle_id,
                start_date if isinstance(start_date, str) else None,
                _instant(vp.get(5, [None])[0]) or header_at,
                lat,
                lon,
            )
        )
    return out


def member_instant(name: str, kind: str) -> datetime | None:
    """The UTC instant an archive member named ``YYYYMMDD/<kind>_HHMMSS.pb`` records."""
    path = PurePosixPath(name)
    match = _MEMBER.match(f"{path.parent.name}/{path.name}")
    if match is None or match["kind"] != kind:
        return None
    try:
        return datetime.strptime(f"{match['day']}{match['clock']}", "%Y%m%d%H%M%S").replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def _archive_polls(path: Path, kind: str) -> Iterator[Poll]:
    """``(file_name, instant named, payload)`` per poll of *kind* in the tar,
    file_name being ``YYYYMMDD/<kind>_HHMMSS.pb`` as ingest names it."""
    with tarfile.open(path, "r:gz") as tf:
        for member in tf:
            if not member.isfile():
                continue
            instant = member_instant(member.name, kind)
            source = tf.extractfile(member) if instant is not None else None
            if instant is None or source is None:
                continue
            p = PurePosixPath(member.name)
            yield f"{p.parent.name}/{p.name}", instant, source.read()


def _too_fast(a: Sighting, b: Sighting) -> bool:
    return _distance(a, b) / _seconds(a.at, b.at) > MAX_SPEED_MPS


def _drop_jumps(fixes: Sequence[Sighting], stats: Counter[str]) -> list[Sighting]:
    """*fixes* without GPS jumps. The track starts at the first fix that one
    of the next two agrees with, so neither a wild first fix nor a wild
    second one throws out the true fixes around it; each later fix is then
    judged against the last one kept."""
    start = next(
        (i for i in range(len(fixes) - 1) if any(not _too_fast(fixes[i], b) for b in fixes[i + 1 : i + 3])),
        len(fixes) - 1,
    )
    stats["gps_jumps_dropped"] += start
    kept = [fixes[start]]
    for s in fixes[start + 1 :]:
        if _too_fast(kept[-1], s):
            stats["gps_jumps_dropped"] += 1
            continue
        kept.append(s)
    return kept


def trip_tracks(
    sightings: Iterable[Sighting], first_departures: Mapping[str, int | None]
) -> tuple[dict[tuple[str, date], list[Fix]], Counter[str]]:
    """Each trip-day's track: its fixes in time order, filed under the service
    date the app files a reading of the trip's first departure under
    (``filed_date_of``). A fix resent by later polls is kept once, and GPS
    jumps are dropped. Where several vehicles, or pieces split by
    TRACK_SPLIT_GAP_SEC, land on one trip-day, the one with most fixes is
    kept. Trips not in *first_departures* are ignored. The stats count, over
    the tracks kept, the gaps between fixes and those too long to interpolate
    across (MAX_BRACKET_GAP_SEC)."""
    by_vehicle: dict[tuple[str, str], dict[datetime, Sighting]] = defaultdict(dict)
    for s in sightings:
        if s.trip_id in first_departures:
            by_vehicle[(s.trip_id, s.vehicle_id)].setdefault(s.at, s)
    stats: Counter[str] = Counter(
        gps_jumps_dropped=0,
        trip_days_with_several_tracks=0,
        start_date_disagreements=0,
        fix_gaps=0,
        fix_gaps_over_bracket=0,
    )
    candidates: dict[tuple[str, date], list[list[Fix]]] = defaultdict(list)
    for (trip_id, _vehicle), fixes in by_vehicle.items():
        ordered = sorted(fixes.values(), key=lambda s: s.at)
        pieces: list[list[Sighting]] = [[ordered[0]]]
        for prev, s in pairwise(ordered):
            if _seconds(prev.at, s.at) > TRACK_SPLIT_GAP_SEC:
                pieces.append([])
            pieces[-1].append(s)
        for piece in pieces:
            kept = _drop_jumps(piece, stats)
            day = filed_date_of(kept[0].at, first_departures[trip_id])
            if kept[0].start_date and kept[0].start_date != f"{day:%Y%m%d}":
                stats["start_date_disagreements"] += 1
            candidates[(trip_id, day)].append([Fix(s.at, s.lat, s.lon) for s in kept])
    tracks = {}
    for key, options in candidates.items():
        if len(options) > 1:
            stats["trip_days_with_several_tracks"] += 1
        tracks[key] = track = max(options, key=len)
        gaps = [_seconds(a.at, b.at) for a, b in pairwise(track)]
        stats["fix_gaps"] += len(gaps)
        stats["fix_gaps_over_bracket"] += sum(1 for g in gaps if g > MAX_BRACKET_GAP_SEC)
    return tracks, stats


def _could_file_into(captured_at: datetime, first: date, last: date) -> bool:
    """Whether any reading of a poll captured at *captured_at* can be filed
    under a date from *first* to *last*: only a capture early in the JST day
    can file under the day before or after its own (``filed_date_of``)."""
    own = service_date_of(captured_at)
    if captured_at.astimezone(_JST).hour >= SERVICE_DAY_CUTOVER_HOURS + FILING_REACH_HOURS:
        return first <= own <= last
    return first - timedelta(days=1) <= own <= last + timedelta(days=1)


def merge_readings(
    kept: dict[date, dict[tuple[str, int], Reading]],
    polls: Iterable[Poll],
    timetable_on: Callable[[date], Timetable | None],
    first: date,
    last: date,
) -> None:
    """Merge into *kept*, by filed date then ``(trip_id, stop_sequence)``, the
    kept reading of each stop event filed from *first* to *last* among the
    ``(file_name, captured_at, payload)`` TripUpdate *polls*, each joined to
    ``timetable_on`` its capture's service day. The pick (see the module
    docstring) is a running maximum, so polls may come in any order and over
    several calls."""
    for file_name, captured_at, payload in polls:
        if not _could_file_into(captured_at, first, last):
            continue
        timetable = timetable_on(service_date_of(captured_at))
        if timetable is None:
            continue
        placed: dict[int | None, tuple[bool, date]] = {}
        for trip in decode_trip_updates(payload):
            seconds = timetable.seconds.get(trip.trip_id, {})
            reports: list[tuple[int, int | None, bool]]
            if is_stopless_cancellation(trip):
                stop_times: list[tuple[int, str | None, str | None]] = [
                    (st.stop_sequence, st.stop_id, st.departure_time)
                    for st in timetable.trips.get(trip.trip_id, ())
                    if _uint16(st.stop_sequence) is not None
                ]
                reports = [(seq, None, True) for seq, _stop, _dep in cancellation_withdrawals(stop_times, captured_at)]
            else:
                trip_withdrawn = trip.schedule_relationship == TRIP_CANCELED
                reports = [
                    (u.stop_sequence, u.dep_delay, trip_withdrawn or u.schedule_relationship == STOP_SKIPPED)
                    for u in trip.stop_time_updates
                    if u.stop_sequence is not None
                ]
            for stop_sequence, dep_delay, withdrawn in reports:
                plausible = dep_delay is not None and -MAX_PLAUSIBLE_DELAY_SEC <= dep_delay <= MAX_PLAUSIBLE_DELAY_SEC
                if not (plausible or withdrawn):
                    continue
                scheduled_sec = seconds.get(stop_sequence)
                if scheduled_sec not in placed:
                    placed[scheduled_sec] = (
                        timely_reading(captured_at, scheduled_sec),
                        filed_date_of(captured_at, scheduled_sec),
                    )
                timely, filed = placed[scheduled_sec]
                if not timely or not first <= filed <= last:
                    continue
                events = kept[filed] if filed in kept else kept.setdefault(filed, {})
                key = (trip.trip_id, stop_sequence)
                current = events.get(key)
                if current is None or (captured_at, file_name) > (current.captured_at, current.file_name):
                    events[key] = Reading(captured_at, file_name, None if withdrawn else dep_delay)


def kept_readings(polls: Iterable[Poll], timetable: Timetable, service_date: date) -> dict[tuple[str, int], Reading]:
    """:func:`merge_readings` for one service date and one timetable."""
    kept: dict[date, dict[tuple[str, int], Reading]] = {}
    merge_readings(kept, polls, lambda _day: timetable, service_date, service_date)
    return kept.get(service_date, {})


def _zip_member(zf: zipfile.ZipFile, name: str) -> str | None:
    """The member whose base name is *name*, the shallowest if a zip nests its files."""
    found = sorted((m for m in zf.namelist() if PurePosixPath(m).name == name), key=lambda m: m.count("/"))
    return found[0] if found else None


def _csv_rows(zf: zipfile.ZipFile, name: str) -> Iterator[dict[str, str]]:
    member = _zip_member(zf, name)
    if member is None:
        return
    with zf.open(member) as raw:
        for row in csv.DictReader(io.TextIOWrapper(raw, encoding="utf-8-sig")):
            yield {k.strip(): (v or "").strip() for k, v in row.items() if k}


def read_timetable(path: Path) -> Timetable:
    stops = {}
    trips: dict[str, list[StopTime]] = defaultdict(list)
    with zipfile.ZipFile(path) as zf:
        for row in _csv_rows(zf, "stops.txt"):
            try:
                stops[row["stop_id"]] = (float(row["stop_lat"]), float(row["stop_lon"]))
            except (KeyError, ValueError):
                continue
        for row in _csv_rows(zf, "stop_times.txt"):
            try:
                stop_sequence = int(row["stop_sequence"])
            except (KeyError, ValueError):
                continue
            timepoint = (row["timepoint"] != "0") if "timepoint" in row else None
            stop_time = StopTime(stop_sequence, row.get("stop_id", ""), row.get("departure_time") or None, timepoint)
            trips[row.get("trip_id", "")].append(stop_time)
    return Timetable(stops, {t: tuple(sorted(sts, key=lambda st: st.stop_sequence)) for t, sts in trips.items()})


def compare(
    service_date: date,
    timetable: Timetable,
    kept: Mapping[tuple[str, int], Reading],
    tracks: Mapping[tuple[str, date], Sequence[Fix]],
) -> tuple[list[Event], Counter[str]]:
    """The measured events of *service_date* and its coverage counts."""
    coverage: Counter[str] = Counter()
    events: list[Event] = []
    found: dict[str, tuple[dict[int, datetime], dict[int, str]]] = {}
    for (trip_id, stop_sequence), reading in sorted(kept.items()):
        if reading.dep_delay is None:
            coverage["withdrawn"] += 1
            continue
        coverage["stop_events"] += 1
        stop_times = timetable.trips.get(trip_id, ())
        stop_time = next((st for st in stop_times if st.stop_sequence == stop_sequence), None)
        if stop_time is None or stop_time.scheduled_sec is None:
            coverage["without_schedule"] += 1
            continue
        track = tracks.get((trip_id, service_date))
        if not track:
            coverage["trip_without_track"] += 1
            continue
        if stop_time.stop_id not in timetable.stops:
            coverage["stop_without_geometry"] += 1
            continue
        if trip_id not in found:
            found[trip_id] = passages_explained(track, _stop_points(service_date, timetable, stop_times))
        trip_passages, why = found[trip_id]
        passed = trip_passages.get(stop_sequence)
        if passed is None:
            coverage["no_passage"] += 1
            coverage[f"no_passage_{why[stop_sequence]}"] += 1
            continue
        coverage["with_passage"] += 1
        scheduled = _scheduled_instant(service_date, stop_time.scheduled_sec)
        lead = _seconds(reading.captured_at, passed)
        if lead < 0:
            coverage["kept_after_passage"] += 1
        events.append(
            Event(
                trip_id,
                service_date,
                stop_sequence,
                stop_time.scheduled_sec,
                stop_time.timepoint,
                reading.dep_delay,
                _seconds(scheduled, passed),
                lead,
            )
        )
    return events, coverage


def _scheduled_instant(service_date: date, scheduled_sec: int) -> datetime:
    return service_day_moment_utc(service_date, service_day_offset(scheduled_sec))


def _stop_points(service_date: date, timetable: Timetable, stop_times: Sequence[StopTime]) -> list[StopPoint]:
    last = stop_times[-1].stop_sequence
    return [
        StopPoint(
            st.stop_sequence,
            *timetable.stops[st.stop_id],
            _scheduled_instant(service_date, st.scheduled_sec),
            st.stop_sequence == last,
        )
        for st in stop_times
        if st.scheduled_sec is not None and st.stop_id in timetable.stops
    ]


def lead_bucket(lead_sec: float) -> int:
    """The first second of the lead group *lead_sec* falls in: the largest
    multiple of LEAD_WIDTH_SEC not above it, held to ±LEAD_CAP_SEC."""
    start = math.floor(lead_sec / LEAD_WIDTH_SEC) * LEAD_WIDTH_SEC
    return max(-LEAD_CAP_SEC, min(LEAD_CAP_SEC, start))


def _stats(errors: Sequence[float]) -> dict[str, Any]:
    if not errors:
        return {"n": 0, "mae_sec": None, "bias_sec": None, "p90_abs_error_sec": None}
    absolute = sorted(abs(e) for e in errors)
    return {
        "n": len(errors),
        "mae_sec": round(sum(absolute) / len(errors), 1),
        "bias_sec": round(sum(errors) / len(errors), 1),
        "p90_abs_error_sec": round(absolute[math.ceil(0.9 * len(absolute)) - 1], 1),
    }


def _grouped(events: Sequence[Event], key: Callable[[Event], Any]) -> list[tuple[Any, dict[str, Any]]]:
    groups: dict[Any, list[float]] = defaultdict(list)
    for e in events:
        groups[key(e)].append(e.error_sec)
    return [(k, _stats(groups[k])) for k in sorted(groups)]


def on_time_class(delay_sec: float) -> str:
    """Which of ON_TIME_CLASSES *delay_sec* falls in under ON_TIME_WINDOW,
    its bounds on time as the app counts them."""
    early, late = ON_TIME_WINDOW
    if early is not None and delay_sec < -early:
        return "early"
    return "late" if delay_sec > late else "on_time"


def on_time_agreement(events: Sequence[Event]) -> dict[str, Any]:
    """How the stop events split into ON_TIME_CLASSES by their kept delay and
    by their observed one, in percent, and how many of each kept class fell
    in each observed one. Each direction is shown apart, since a bias moves
    events into on time from one side as it moves them out on the other."""
    counts = Counter((on_time_class(e.predicted_delay_sec), on_time_class(e.observed_delay_sec)) for e in events)
    n = len(events)

    def pct(count: int) -> float | None:
        return round(100 * count / n, 1) if n else None

    return {
        "n": n,
        "kept_pct": {c: pct(sum(v for (k, _o), v in counts.items() if k == c)) for c in ON_TIME_CLASSES},
        "observed_pct": {c: pct(sum(v for (_k, o), v in counts.items() if o == c)) for c in ON_TIME_CLASSES},
        "agreement_pct": pct(sum(v for (k, o), v in counts.items() if k == o)),
        "kept_to_observed": {k: {o: counts[(k, o)] for o in ON_TIME_CLASSES} for k in ON_TIME_CLASSES},
    }


def figures(events: Sequence[Event]) -> dict[str, Any]:
    def lead_range(start: int) -> dict[str, int | None]:
        return {
            "lead_from_sec": None if start == -LEAD_CAP_SEC else start,
            "lead_to_sec": None if start == LEAD_CAP_SEC else start + LEAD_WIDTH_SEC,
        }

    def timepoint(e: Event) -> str:
        return "unknown" if e.timepoint is None else ("yes" if e.timepoint else "no")

    return {
        "overall": _stats([e.error_sec for e in events]),
        "by_lead": [{**lead_range(k), **s} for k, s in _grouped(events, lambda e: lead_bucket(e.lead_sec))],
        "by_hour": [
            {"hour": k, **s} for k, s in _grouped(events, lambda e: service_day_offset(e.scheduled_sec) // 3600)
        ],
        "by_timepoint": [{"timepoint": k, **s} for k, s in _grouped(events, timepoint)],
        "on_time": on_time_agreement(events),
    }


def _day(text: str) -> date:
    return datetime.strptime(text, "%Y%m%d").date()


def _service_dates(first: date, last: date) -> list[date]:
    return [first + timedelta(days=i) for i in range((last - first).days + 1)]


def _archive_days(kind: str, service_date: date) -> tuple[date, ...]:
    """The UTC days whose *kind* archives hold every capture filed under
    *service_date*. A TripUpdates poll whose feed header is unusable is
    stamped from its name read as JST (``archive_captured_at``), nine hours
    early, so a poll of UTC day D+1 can file under D. A vehicle position is
    timed by its own fix, which UTC days D-1 and D hold."""
    days = (service_date - timedelta(days=1), service_date)
    return (*days, service_date + timedelta(days=1)) if kind == "rt" else days


def _timetable_for(names: Iterable[str], service_date: date) -> str | None:
    dated = [(_day(m["day"]), n) for n in names if (m := _STATIC_NAME.match(n))]
    on_or_before = [d for d in dated if d[0] <= service_date]
    return max(on_or_before)[1] if on_or_before else None


def _trip_update_polls(path: Path) -> Iterator[Poll]:
    for file_name, _named, payload in _archive_polls(path, "TripUpdate"):
        day, pb_name = file_name.split("/", 1)
        yield file_name, datetime.fromisoformat(archive_captured_at(payload, day, pb_name)), payload


def validate(agency_id: int, first: date, last: date, root: Path) -> dict[str, Any]:
    """Read the TripUpdates archives of the UTC days *first* - 1 to *last* + 1
    in order, each once. Service date D is complete once UTC day D+1 is read
    (``_archive_days``), so it is then measured against its tracks and its
    kept readings dropped. A reading filed under a date already measured is
    counted as ``readings_after_measurement`` rather than lost silently."""
    static_dir = root / "static" / str(agency_id)
    static_names = sorted(p.name for p in static_dir.glob("gtfs_static_*.zip")) if static_dir.is_dir() else []
    loaded: dict[str, Timetable] = {}

    def timetable_on(day: date) -> Timetable | None:
        name = _timetable_for(static_names, day)
        if name is not None and name not in loaded:
            loaded[name] = read_timetable(static_dir / name)
        return None if name is None else loaded[name]

    def archive(kind: str, day: date) -> Path:
        return root / kind / str(agency_id) / f"{day:%Y%m%d}.tar.gz"

    kept: dict[date, dict[tuple[str, int], Reading]] = {}
    events: list[Event] = []
    coverage: Counter[str] = Counter()
    track_stats: Counter[str] = Counter()
    days = []
    measured: set[date] = set()
    utc_days = [first - timedelta(days=1), *_service_dates(first, last), last + timedelta(days=1)]
    for utc_day in utc_days:
        if archive("rt", utc_day).exists():
            merge_readings(kept, _trip_update_polls(archive("rt", utc_day)), timetable_on, first, last)
        service_date = utc_day - timedelta(days=1)
        if service_date < first:
            continue
        measured.add(service_date)
        day_kept = kept.pop(service_date, {})
        timetable = timetable_on(service_date)
        paths = [archive(kind, d) for kind in ("rt", "vp") for d in _archive_days(kind, service_date)]
        day: dict[str, Any] = {
            "service_date": service_date.isoformat(),
            "timetable": _timetable_for(static_names, service_date),
            "missing_archives": [str(p.relative_to(root)) for p in paths if not p.exists()],
        }
        days.append(day)
        if timetable is None:
            continue
        first_departures = {
            trip_id: next((st.scheduled_sec for st in sts if st.scheduled_sec is not None), None)
            for trip_id, sts in timetable.trips.items()
        }
        sightings = (
            s
            for d in _archive_days("vp", service_date)
            if archive("vp", d).exists()
            for _name, polled_at, payload in _archive_polls(archive("vp", d), "VehiclePosition")
            for s in decode_vehicle_positions(payload, polled_at)
        )
        tracks, stats = trip_tracks(sightings, first_departures)
        track_stats.update(stats)
        day_events, day_coverage = compare(service_date, timetable, day_kept, tracks)
        day.update(stop_events=day_coverage["stop_events"], with_passage=day_coverage["with_passage"])
        events.extend(day_events)
        coverage.update(day_coverage)
    coverage["readings_after_measurement"] = sum(len(kept[d]) for d in kept if d in measured)
    coverage_keys = (
        "stop_events",
        "with_passage",
        "without_schedule",
        "trip_without_track",
        "stop_without_geometry",
        "no_passage",
        "no_passage_gap",
        "no_passage_no_visit",
        "no_passage_unseen",
        "no_passage_implausible",
        "no_passage_delay_jump",
        "no_passage_out_of_order",
        "kept_after_passage",
        "withdrawn",
        "readings_after_measurement",
    )
    return {
        "schema": SCHEMA,
        "agency_id": agency_id,
        "from": first.isoformat(),
        "to": last.isoformat(),
        "thresholds": {
            "passage_radius_m": PASSAGE_RADIUS_M,
            "dwell_tolerance_m": DWELL_TOLERANCE_M,
            "max_bracket_gap_sec": MAX_BRACKET_GAP_SEC,
            "max_delay_change_sec": MAX_DELAY_CHANGE_SEC,
            "delay_drift_share": DELAY_DRIFT_SHARE,
            "max_speed_mps": MAX_SPEED_MPS,
            "min_moving_speed_mps": MIN_MOVING_SPEED_MPS,
            "track_split_gap_sec": TRACK_SPLIT_GAP_SEC,
            "lead_width_sec": LEAD_WIDTH_SEC,
            "lead_cap_sec": LEAD_CAP_SEC,
            "on_time_early_tolerance_sec": ON_TIME_WINDOW[0],
            "on_time_late_tolerance_sec": ON_TIME_WINDOW[1],
        },
        "coverage": {k: coverage[k] for k in coverage_keys},
        "tracks": dict(sorted(track_stats.items())),
        "figures": figures(events),
        "days": days,
    }


def missing_archives(summary: dict[str, Any]) -> list[str]:
    """Every archive some service date of the run needed and the mirror lacked, once each."""
    return sorted({path for day in summary["days"] for path in day["missing_archives"]})


def markdown(summary: dict[str, Any]) -> str:
    c = summary["coverage"]
    share = f"{100 * c['with_passage'] / c['stop_events']:.1f}%" if c["stop_events"] else "n/a"
    lines = [
        f"# Agency {summary['agency_id']}: kept prediction vs observed passage, {summary['from']} to {summary['to']}",
        "",
        f"Stop events with a reconstructed passage: {c['with_passage']} of {c['stop_events']} ({share}). "
        f"Kept reading captured after the passage: {c['kept_after_passage']}. "
        f"Missing archives: {len(missing_archives(summary))} (listed per day in the JSON).",
        "",
        "Error = kept delay - observed delay (positive: the feed reported the bus later than it left).",
        "",
        "| group | n | MAE s | bias s | p90 abs s |",
        "|---|---|---|---|---|",
    ]

    def row(label: str, s: dict[str, Any]) -> str:
        return f"| {label} | {s['n']} | {s['mae_sec']} | {s['bias_sec']} | {s['p90_abs_error_sec']} |"

    f = summary["figures"]
    lines.append(row("overall", f["overall"]))
    for g in f["by_lead"]:
        lo = "" if g["lead_from_sec"] is None else g["lead_from_sec"]
        hi = "" if g["lead_to_sec"] is None else g["lead_to_sec"]
        lines.append(row(f"lead [{lo}, {hi}) s", g))
    lines += [row(f"hour {g['hour']}", g) for g in f["by_hour"]]
    lines += [row(f"timepoint {g['timepoint']}", g) for g in f["by_timepoint"]]
    on_time = f["on_time"]
    thresholds = summary["thresholds"]
    early, late = thresholds["on_time_early_tolerance_sec"], thresholds["on_time_late_tolerance_sec"]
    window = f"no more than {late} s late" + ("" if early is None else f" and no more than {early} s early")
    lines += [
        "",
        f"On time = {window}. Each measured stop event classed by its kept delay and by its observed one; "
        f"the same class for both: {on_time['agreement_pct']}%.",
        "",
        "| class | kept % | observed % |",
        "|---|---|---|",
        *(f"| {k} | {on_time['kept_pct'][k]} | {on_time['observed_pct'][k]} |" for k in ON_TIME_CLASSES),
        "",
        "| kept \\ observed | " + " | ".join(ON_TIME_CLASSES) + " |",
        "|---|" + "---|" * len(ON_TIME_CLASSES),
        *(
            f"| {k} | " + " | ".join(str(on_time["kept_to_observed"][k][o]) for o in ON_TIME_CLASSES) + " |"
            for k in ON_TIME_CLASSES
        ),
    ]
    return "\n".join(lines) + "\n"


def _required(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        raise SystemExit(f"{name} must be set, not empty, for --fetch")
    return value


def quiet_run(cmd: Sequence[str]) -> None:
    """Run *cmd*, dropping its output: the aws CLI's messages can name the
    bucket's endpoint. A nonzero exit raises CalledProcessError."""
    subprocess.run(list(cmd), check=True, capture_output=True)


def fetch_archives(agency_id: int, first: date, last: date, root: Path, *, run: Run, capture: Capture) -> None:
    """Download into *root* the archives and timetables the service dates need
    that R2 holds and *root* lacks. A failed download is reported and skipped."""
    bucket, endpoint = _required("OBJECT_STORE_BUCKET"), _required("OBJECT_STORE_ENDPOINT")

    def listed(prefix: str) -> list[str]:
        try:
            return list_keys(bucket, prefix, endpoint, capture=capture)
        except subprocess.CalledProcessError as exc:
            raise SystemExit(f"listing {prefix} failed (exit {exc.returncode})") from exc

    wanted: set[str] = set()
    for kind in ("rt", "vp"):
        held = set(listed(f"{kind}/{agency_id}/"))
        for service_date in _service_dates(first, last):
            wanted |= {f"{kind}/{agency_id}/{d:%Y%m%d}.tar.gz" for d in _archive_days(kind, service_date)} & held
    statics = {PurePosixPath(k).name: k for k in listed(f"static/{agency_id}/")}
    for service_date in _service_dates(first, last):
        name = _timetable_for(statics, service_date)
        if name is not None:
            wanted.add(statics[name])
    for key in sorted(wanted):
        dest = root / key
        if dest.exists():
            continue
        dest.parent.mkdir(parents=True, exist_ok=True)
        partial = dest.with_name(dest.name + ".partial")
        try:
            download_file(bucket, key, partial, endpoint, run=run)
            partial.replace(dest)
        except subprocess.CalledProcessError as exc:
            sys.stderr.write(f"{key}: download failed (exit {exc.returncode})\n")
            partial.unlink(missing_ok=True)


def _parse_args(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n", 1)[0] if __doc__ else None)
    parser.add_argument("--agency", type=int, required=True, help="agency id")
    parser.add_argument("--from", dest="first", type=date.fromisoformat, required=True, help="first service date")
    parser.add_argument("--to", dest="last", type=date.fromisoformat, required=True, help="last service date")
    parser.add_argument("--archives", type=Path, required=True, help="local mirror of R2's rt/, vp/ and static/")
    parser.add_argument(
        "--out",
        type=Path,
        default=Path.home() / ".cache" / "transit-app" / "vp-validation",
        help="where <id>/<FROM>_<TO>.json and .md are written",
    )
    parser.add_argument("--fetch", action="store_true", help="first download from R2 what the mirror lacks")
    args = parser.parse_args(argv)
    if args.last < args.first:
        parser.error("--to is before --from")
    return args


def main(argv: Sequence[str] | None = None, *, run: Run = quiet_run, capture: Capture = default_capture) -> int:
    args = _parse_args(argv)
    if args.fetch:
        fetch_archives(args.agency, args.first, args.last, args.archives, run=run, capture=capture)
    summary = validate(args.agency, args.first, args.last, args.archives)
    stem = args.out / str(args.agency) / f"{args.first:%Y%m%d}_{args.last:%Y%m%d}"
    stem.parent.mkdir(parents=True, exist_ok=True)
    stem.with_suffix(".json").write_text(json.dumps(summary, indent=1) + "\n")
    stem.with_suffix(".md").write_text(markdown(summary))
    overall, coverage = summary["figures"]["overall"], summary["coverage"]
    print(
        f"agency {args.agency} {args.first}..{args.last}: {coverage['with_passage']}/{coverage['stop_events']} "
        f"stop events measured, MAE {overall['mae_sec']} s, bias {overall['bias_sec']} s, "
        f"{len(missing_archives(summary))} archives missing -> {stem}.json"
    )
    return 0 if overall["n"] else 1


if __name__ == "__main__":
    sys.exit(main())
