"""Hold the feed's kept TripUpdates prediction against stop passages observed
in archived VehiclePositions, for one agency over a range of service dates.

Every delay the app reports is the operator's prediction, the latest timely
reading the feed sent for a stop event (``pipeline.db.build_dedup_ch_sql``),
not an observation. This measures how far that kept value is from when the
bus actually left, offline, from the collector's R2 archives alone: no
database is read or written.

**Inputs** sit under ``--archives`` as R2 lays them out:
``rt/<id>/<YYYYMMDD>.tar.gz`` (``YYYYMMDD/TripUpdate_HHMMSS.pb``),
``vp/<id>/<YYYYMMDD>.tar.gz`` (``YYYYMMDD/VehiclePosition_HHMMSS.pb``), each
the polls of one UTC day named in UTC, and
``static/<id>/gtfs_static_<YYYYMMDD>.zip``. Service date D is read from the
UTC days D-1 and D, which hold every capture filed under it, against the
newest timetable dated on or before D. ``--fetch`` first downloads whichever
of those R2 holds and the mirror lacks.

**The kept reading** of a stop event, keyed by trip, filed service date and
stop_sequence, is chosen as the app chooses it: of the readings that are
timely (``pipeline.db.timely_reading``) and either carry a delay inside the
plausibility clamp or withdraw the departure (trip CANCELED, stop SKIPPED),
the latest by capture instant then file name. A stop event whose kept reading
withdraws its departure has no departure to measure. The capture instant is
the one archive ingest stamps (``archive_captured_at``).

**The observed passage** comes from the trip's vehicle track: its positions
for that trip and service date, resent fixes and GPS jumps dropped. The
stops are matched in stop_sequence order, each searched for only after the
previous stop's passage and inside a window around its schedule shifted by
the delay last observed on the trip, so a loop's return past its first stop,
a later lap, or a stop beside the road out are not taken for it. A stop's
visit is the first run of track segments that come within PASSAGE_RADIUS_M
of it; of the segments nearest the stop (DWELL_TOLERANCE_M), the last gives
the departure and, at the trip's final stop, the first gives the arrival, so
a bus idling at its first stop departs when it pulls out and one laying over
at its last arrives when it gets there. The instant is interpolated on that
segment: the vehicle is taken to cover the stretch from the stop to its next
fix at the speed it is next seen moving (to its arrival, from its previous
fix at the speed it was last seen moving), which places a departure from a
dwell between two polls rather than at the last fix at the stop. No segment
spanning more than MAX_BRACKET_GAP_SEC is interpolated. A bus last seen
standing at a stop has no departure from it, and a track that ends still
short of the final stop, by more than DWELL_TOLERANCE_M, has no arrival.

**The comparison** per stop event with both: error = kept delay - observed
delay, positive when the feed reported the bus later than it left; lead =
passage - capture of the kept reading, negative when that reading came after
the bus had left. The summary gives MAE, bias and the 90th percentile of the
absolute error overall, by lead (LEAD_WIDTH_SEC groups, open beyond
±LEAD_CAP_SEC), by service-day hour of the scheduled departure and by
timepoint ("unknown" where stop_times.txt has no timepoint column), plus
coverage: how many kept stop events had a passage, and why the rest did not.

Output: ``<out>/<id>/<FROM>_<TO>.json`` and a Markdown table beside it as
``.md``. The run exits 1 when no stop event could be compared.

Credentials (``--fetch`` only): the transit-archives pair in
AWS_ACCESS_KEY_ID / AWS_SECRET_ACCESS_KEY, with OBJECT_STORE_BUCKET and
OBJECT_STORE_ENDPOINT, as ``scripts/rt_validate_sample.py`` reads them. A
failed download is reported by key and exit status only.

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
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from itertools import pairwise
from pathlib import Path, PurePosixPath
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from ml.r2 import default_capture, default_run, download_file, list_keys  # noqa: E402  (sys.path injection above)
from pipeline.clickhouse import STOP_SKIPPED, TRIP_CANCELED  # noqa: E402
from pipeline.db import MAX_PLAUSIBLE_DELAY_SEC, timely_reading  # noqa: E402
from pipeline.service_day import filed_date_of, service_day_moment_utc, service_day_offset  # noqa: E402
from pipeline.strategies._pb import _dec, _fields, archive_captured_at, decode_feed_timestamp  # noqa: E402
from pipeline.strategies._time import parse_departure_time  # noqa: E402
from pipeline.strategies.static_join import _decode_trip_updates  # noqa: E402

SCHEMA = 1

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
# The longest pair of consecutive fixes a passage is interpolated between:
# four missed 20 s polls. Over a longer gap the bus may have stopped, turned
# or queued anywhere, so no instant inside it is believable.
MAX_BRACKET_GAP_SEC = 90
# How far from its schedule, shifted by the delay observed at the trip's
# previous passage, a stop's visit may start. A delay rarely moves by
# minutes between adjacent stops, while a loop or a figure-eight comes back
# past the same place a lap later. Before the trip's first passage the
# window runs from this much early to MAX_PLAUSIBLE_DELAY_SEC late, the
# app's own clamp.
TRACK_WINDOW_SEC = 900
# Faster than any bus: a fix implying more is a GPS jump and is dropped.
MAX_SPEED_MPS = 40.0
# Below this a segment is a bus creeping or standing, too slow to time the
# stretch from a stop to the next fix by.
MIN_MOVING_SPEED_MPS = 1.0
# The same trip_id runs on every service day; fixes this far apart belong to
# runs on different days, while a single run never pauses this long.
TRACK_SPLIT_GAP_SEC = 3 * 3600

# Lead-time groups. The feeds are polled every 20-30 s, so a minute holds
# the last poll or two before a passage; past ten minutes the groups are
# pooled, since few kept readings are that old.
LEAD_WIDTH_SEC = 60
LEAD_CAP_SEC = 600

_MEMBER = re.compile(r"^(?P<day>\d{8})/(?P<kind>TripUpdate|VehiclePosition)_(?P<clock>\d{6})\.pb$")
_RT_OR_VP_KEY = re.compile(r"^(?P<kind>rt|vp)/(?P<agency>\d+)/(?P<day>\d{8})\.tar\.gz$")
_STATIC_NAME = re.compile(r"^gtfs_static_(?P<day>\d{8})\.zip$")

Run = Callable[[Sequence[str]], None]
Capture = Callable[[Sequence[str]], str]


@dataclass(frozen=True)
class Fix:
    at: datetime
    lat: float
    lon: float


@dataclass(frozen=True)
class StopPoint:
    """A stop of a trip to find the passage of. ``final`` marks the trip's
    last stop, whose passage is its arrival."""

    stop_sequence: int
    lat: float
    lon: float
    scheduled: datetime
    final: bool = False


@dataclass(frozen=True)
class Sighting:
    trip_id: str
    vehicle_id: str
    start_date: str | None
    at: datetime
    lat: float
    lon: float


@dataclass(frozen=True)
class StopTime:
    """``scheduled_sec`` is the departure_time's raw seconds as ingest stores
    them, None where the timetable gives none; ``timepoint`` is None where
    the timetable has no timepoint column."""

    stop_sequence: int
    stop_id: str
    scheduled_sec: int | None
    timepoint: bool | None


@dataclass(frozen=True)
class Timetable:
    stops: dict[str, tuple[float, float]]
    trips: dict[str, tuple[StopTime, ...]]


@dataclass(frozen=True)
class Reading:
    """A stop event's kept reading; ``dep_delay`` is None when it withdraws the departure."""

    captured_at: datetime
    file_name: str
    dep_delay: int | None


@dataclass(frozen=True)
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


@dataclass(frozen=True)
class _Approach:
    segment: int
    fraction: float
    distance: float


def _xy(lat: float, lon: float, origin_lat: float, origin_lon: float) -> tuple[float, float]:
    return (
        (lon - origin_lon) * METERS_PER_DEGREE * math.cos(math.radians(origin_lat)),
        (lat - origin_lat) * METERS_PER_DEGREE,
    )


def _distance(a: Fix | StopPoint, b: Fix | StopPoint) -> float:
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


def _visit(
    track: Sequence[Fix], stop: StopPoint, cursor: int, floor: float, lo: datetime, hi: datetime
) -> list[_Approach]:
    """The first run of consecutive usable segments within PASSAGE_RADIUS_M of
    *stop*, from segment *cursor* (past fraction *floor* of it) on, its first
    segment overlapping [*lo*, *hi*]. A dwell may run on past *hi*."""
    run: list[_Approach] = []
    for j in range(cursor, len(track) - 1):
        usable = _usable(track, j)
        if run:
            approach = _approach(track, j, stop, 0.0) if usable else None
            if approach is None or approach.distance > PASSAGE_RADIUS_M:
                break
            run.append(approach)
            continue
        if track[j].at > hi:
            break
        if not usable or track[j + 1].at < lo:
            continue
        approach = _approach(track, j, stop, floor if j == cursor else 0.0)
        if approach.distance <= PASSAGE_RADIUS_M:
            run.append(approach)
    return run


def _nearest(visit: Sequence[_Approach]) -> list[_Approach]:
    closest = min(a.distance for a in visit)
    return [a for a in visit if a.distance <= closest + DWELL_TOLERANCE_M]


def _departure(track: Sequence[Fix], visit: Sequence[_Approach], stop: StopPoint) -> _Approach | None:
    nearest = _nearest(visit)
    last = nearest[-1]
    b = track[last.segment + 1]
    still_there = _distance(b, stop) <= min(x.distance for x in nearest) + DWELL_TOLERANCE_M
    if still_there and not _usable(track, last.segment + 1):
        return None
    return last


def _departure_instant(track: Sequence[Fix], last: _Approach) -> datetime:
    a, b = track[last.segment], track[last.segment + 1]
    span = _seconds(a.at, b.at)
    speed = _speed(track, last.segment + 1)
    if speed is None:
        offset = last.fraction * span
    else:
        offset = span - (1 - last.fraction) * _distance(a, b) / speed
    return a.at + timedelta(seconds=min(span, max(0.0, offset)))


def _arrival(track: Sequence[Fix], visit: Sequence[_Approach], stop: StopPoint) -> _Approach | None:
    nearest = _nearest(visit)
    first = nearest[0]
    a = track[first.segment]
    already_there = _distance(a, stop) <= min(x.distance for x in nearest) + DWELL_TOLERANCE_M
    if already_there and not _usable(track, first.segment - 1):
        return None
    still_coming = first.fraction == 1.0 and first.distance > DWELL_TOLERANCE_M
    if still_coming and not _usable(track, first.segment + 1):
        return None
    return first


def _arrival_instant(track: Sequence[Fix], first: _Approach) -> datetime:
    a, b = track[first.segment], track[first.segment + 1]
    span = _seconds(a.at, b.at)
    speed = _speed(track, first.segment - 1)
    if speed is None:
        offset = first.fraction * span
    else:
        offset = first.fraction * _distance(a, b) / speed
    return a.at + timedelta(seconds=min(span, max(0.0, offset)))


def passages(track: Sequence[Fix], stops: Sequence[StopPoint]) -> dict[int, datetime]:
    """The observed passage of each of *stops* (in stop_sequence order) that
    the time-ordered *track* shows, by stop_sequence: the departure, or the
    arrival at a ``final`` stop. See the module docstring for the rule."""
    found: dict[int, datetime] = {}
    cursor, floor = 0, 0.0
    shift: float | None = None
    for stop in stops:
        if shift is None:
            lo = stop.scheduled - timedelta(seconds=TRACK_WINDOW_SEC)
            hi = stop.scheduled + timedelta(seconds=MAX_PLAUSIBLE_DELAY_SEC)
        else:
            expected = stop.scheduled + timedelta(seconds=shift)
            lo, hi = expected - timedelta(seconds=TRACK_WINDOW_SEC), expected + timedelta(seconds=TRACK_WINDOW_SEC)
        visit = _visit(track, stop, cursor, floor, lo, hi)
        if not visit:
            continue
        chosen = (_arrival if stop.final else _departure)(track, visit, stop)
        if chosen is None:
            continue
        at = (_arrival_instant if stop.final else _departure_instant)(track, chosen)
        found[stop.stop_sequence] = at
        cursor, floor = chosen.segment, chosen.fraction
        shift = _seconds(stop.scheduled, at)
    return found


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


def _archive_polls(path: Path, kind: str) -> Iterator[tuple[str, datetime, bytes]]:
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


def _drop_jumps(fixes: Sequence[Sighting], stats: Counter[str]) -> list[Sighting]:
    kept = [fixes[0]]
    for s in fixes[1:]:
        prev = kept[-1]
        apart = math.hypot(*_xy(s.lat, s.lon, prev.lat, prev.lon))
        if apart / _seconds(prev.at, s.at) > MAX_SPEED_MPS:
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
    kept. Trips not in *first_departures* are ignored."""
    by_vehicle: dict[tuple[str, str], dict[datetime, Sighting]] = defaultdict(dict)
    for s in sightings:
        if s.trip_id in first_departures:
            by_vehicle[(s.trip_id, s.vehicle_id)].setdefault(s.at, s)
    stats: Counter[str] = Counter(gps_jumps_dropped=0, trip_days_with_several_tracks=0, start_date_disagreements=0)
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
        tracks[key] = max(options, key=len)
    return tracks, stats


def kept_readings(
    polls: Iterable[tuple[str, datetime, bytes]], timetable: Timetable, service_date: date
) -> dict[tuple[str, int], Reading]:
    """The kept reading of each stop event filed under *service_date*, by
    ``(trip_id, stop_sequence)``, from ``(file_name, captured_at, payload)``
    TripUpdate polls. See the module docstring for the rule."""
    schedule = {trip_id: {st.stop_sequence: st.scheduled_sec for st in sts} for trip_id, sts in timetable.trips.items()}
    kept: dict[tuple[str, int], Reading] = {}
    for file_name, captured_at, payload in polls:
        for trip_id, _route, trip_status, updates in _decode_trip_updates(payload):
            seconds = schedule.get(trip_id, {})
            for stop_sequence, dep_delay, _stop_id, _arr_delay, stop_status in updates:
                if stop_sequence is None:
                    continue
                withdrawn = trip_status == TRIP_CANCELED or stop_status == STOP_SKIPPED
                plausible = dep_delay is not None and -MAX_PLAUSIBLE_DELAY_SEC <= dep_delay <= MAX_PLAUSIBLE_DELAY_SEC
                scheduled_sec = seconds.get(stop_sequence)
                if not (plausible or withdrawn) or not timely_reading(captured_at, scheduled_sec):
                    continue
                if filed_date_of(captured_at, scheduled_sec) != service_date:
                    continue
                key = (trip_id, stop_sequence)
                current = kept.get(key)
                if current is None or (captured_at, file_name) > (current.captured_at, current.file_name):
                    kept[key] = Reading(captured_at, file_name, None if withdrawn else dep_delay)
    return kept


def _zip_member(zf: zipfile.ZipFile, name: str) -> str | None:
    """The member whose base name is *name*, the shallowest if a zip nests its files."""
    found = sorted((m for m in zf.namelist() if PurePosixPath(m).name == name), key=lambda m: m.count("/"))
    return found[0] if found else None


def _csv_rows(zf: zipfile.ZipFile, name: str) -> tuple[list[str], list[dict[str, str]]]:
    member = _zip_member(zf, name)
    if member is None:
        return [], []
    with zf.open(member) as raw:
        reader = csv.DictReader(io.TextIOWrapper(raw, encoding="utf-8-sig"))
        rows = [{k.strip(): (v or "").strip() for k, v in row.items() if k} for row in reader]
        return [f.strip() for f in reader.fieldnames or []], rows


def read_timetable(path: Path) -> Timetable:
    with zipfile.ZipFile(path) as zf:
        _fields_stops, stop_rows = _csv_rows(zf, "stops.txt")
        columns, time_rows = _csv_rows(zf, "stop_times.txt")
    stops = {}
    for row in stop_rows:
        try:
            stops[row["stop_id"]] = (float(row["stop_lat"]), float(row["stop_lon"]))
        except (KeyError, ValueError):
            continue
    stated = "timepoint" in columns
    trips: dict[str, list[StopTime]] = defaultdict(list)
    for row in time_rows:
        try:
            stop_sequence = int(row["stop_sequence"])
        except (KeyError, ValueError):
            continue
        timepoint = (row.get("timepoint") != "0") if stated else None
        scheduled_sec = parse_departure_time(row.get("departure_time") or None)[2]
        trips[row.get("trip_id", "")].append(StopTime(stop_sequence, row.get("stop_id", ""), scheduled_sec, timepoint))
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
    found: dict[str, dict[int, datetime]] = {}
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
            found[trip_id] = passages(track, _stop_points(service_date, timetable, stop_times))
        passed = found[trip_id].get(stop_sequence)
        if passed is None:
            coverage["no_passage"] += 1
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
    }


def _day(text: str) -> date:
    return datetime.strptime(text, "%Y%m%d").date()


def _service_dates(first: date, last: date) -> list[date]:
    return [first + timedelta(days=i) for i in range((last - first).days + 1)]


def _archive_days(service_date: date) -> tuple[date, date]:
    """The UTC days whose archives hold every capture filed under *service_date*."""
    return service_date - timedelta(days=1), service_date


def _timetable_for(names: Iterable[str], service_date: date) -> str | None:
    dated = [(_day(m["day"]), n) for n in names if (m := _STATIC_NAME.match(n))]
    on_or_before = [d for d in dated if d[0] <= service_date]
    return max(on_or_before)[1] if on_or_before else None


def validate(agency_id: int, first: date, last: date, root: Path) -> dict[str, Any]:
    static_dir = root / "static" / str(agency_id)
    static_names = sorted(p.name for p in static_dir.glob("gtfs_static_*.zip")) if static_dir.is_dir() else []
    loaded: dict[str, Timetable] = {}
    events: list[Event] = []
    coverage: Counter[str] = Counter()
    track_stats: Counter[str] = Counter()
    days = []
    for service_date in _service_dates(first, last):
        name = _timetable_for(static_names, service_date)
        archives = {
            kind: [root / kind / str(agency_id) / f"{d:%Y%m%d}.tar.gz" for d in _archive_days(service_date)]
            for kind in ("rt", "vp")
        }
        day: dict[str, Any] = {
            "service_date": service_date.isoformat(),
            "timetable": name,
            "missing_archives": [str(p.relative_to(root)) for ps in archives.values() for p in ps if not p.exists()],
        }
        days.append(day)
        if name is None:
            continue
        if name not in loaded:
            loaded[name] = read_timetable(static_dir / name)
        timetable = loaded[name]
        first_departures = {
            trip_id: next((st.scheduled_sec for st in sts if st.scheduled_sec is not None), None)
            for trip_id, sts in timetable.trips.items()
        }
        sightings = [
            s
            for path in archives["vp"]
            if path.exists()
            for _name, polled_at, payload in _archive_polls(path, "VehiclePosition")
            for s in decode_vehicle_positions(payload, polled_at)
        ]
        tracks, stats = trip_tracks(sightings, first_departures)
        track_stats.update(stats)
        kept = kept_readings(_trip_update_polls(archives["rt"]), timetable, service_date)
        day_events, day_coverage = compare(service_date, timetable, kept, tracks)
        day.update(stop_events=day_coverage["stop_events"], with_passage=day_coverage["with_passage"])
        events.extend(day_events)
        coverage.update(day_coverage)
    coverage_keys = (
        "stop_events",
        "with_passage",
        "without_schedule",
        "trip_without_track",
        "stop_without_geometry",
        "no_passage",
        "kept_after_passage",
        "withdrawn",
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
            "track_window_sec": TRACK_WINDOW_SEC,
            "max_speed_mps": MAX_SPEED_MPS,
            "lead_width_sec": LEAD_WIDTH_SEC,
            "lead_cap_sec": LEAD_CAP_SEC,
        },
        "coverage": {k: coverage[k] for k in coverage_keys},
        "tracks": dict(sorted(track_stats.items())),
        "figures": figures(events),
        "days": days,
    }


def _trip_update_polls(paths: Iterable[Path]) -> Iterator[tuple[str, datetime, bytes]]:
    for path in paths:
        if not path.exists():
            continue
        for file_name, _named, payload in _archive_polls(path, "TripUpdate"):
            day, pb_name = file_name.split("/", 1)
            yield file_name, datetime.fromisoformat(archive_captured_at(payload, day, pb_name)), payload


def markdown(summary: dict[str, Any]) -> str:
    c = summary["coverage"]
    share = f"{100 * c['with_passage'] / c['stop_events']:.1f}%" if c["stop_events"] else "n/a"
    lines = [
        f"# Agency {summary['agency_id']}: kept prediction vs observed passage, {summary['from']} to {summary['to']}",
        "",
        f"Stop events with a reconstructed passage: {c['with_passage']} of {c['stop_events']} ({share}). "
        f"Kept reading captured after the passage: {c['kept_after_passage']}.",
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
    return "\n".join(lines) + "\n"


def _required(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        raise SystemExit(f"{name} must be set, not empty, for --fetch")
    return value


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
            wanted |= {f"{kind}/{agency_id}/{d:%Y%m%d}.tar.gz" for d in _archive_days(service_date)} & held
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
            # The command line carries the bucket's endpoint; the exit status is enough.
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


def main(argv: Sequence[str] | None = None, *, run: Run = default_run, capture: Capture = default_capture) -> int:
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
        f"stop events measured, MAE {overall['mae_sec']} s, bias {overall['bias_sec']} s -> {stem}.json"
    )
    return 0 if overall["n"] else 1


if __name__ == "__main__":
    sys.exit(main())
