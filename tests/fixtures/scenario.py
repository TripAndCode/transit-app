"""Scenarios: a declared operation with known truth, emitted as the static
GTFS and the GTFS-RT polls a real feed would publish for it.

A scenario declares routes as ordered stops, a timetable of trips with
scheduled departures on the GTFS service-day clock (``24:10:00`` is ten past
midnight at the end of the service date), the service days it covers with the
day type each runs, and the truth: when every trip run actually left every
stop. A feed model turns the truth into the TripUpdate polls a collector
captures. ``tests.fixtures.scenario_oracle`` computes the expected figures from
the truth alone, so a test can push the polls through the real ingest path and
compare what the pipeline reports with what happened.

Every scheduled trip of a declared day has a run: a trip the feed never
reports would be a cancellation, which this model does not declare yet.
"""

from __future__ import annotations

import csv
import io
import itertools
import math
import zipfile
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta, timezone
from typing import Literal
from zoneinfo import ZoneInfo

from tests.fixtures.gtfs_rt import stop_time_update, trip_update, trip_update_feed

JST = ZoneInfo("Asia/Tokyo")


def gtfs_seconds(clock: str) -> int:
    """Seconds since the service date's midnight for a GTFS ``HH:MM:SS``;
    the hour may exceed 23."""
    hours, minutes, seconds = (int(part) for part in clock.split(":"))
    if not (0 <= minutes < 60 and 0 <= seconds < 60) or hours < 0:
        raise ValueError(f"not a GTFS time: {clock!r}")
    return hours * 3600 + minutes * 60 + seconds


def gtfs_clock(seconds: int) -> str:
    """The GTFS ``HH:MM:SS`` for *seconds* since the service date's midnight."""
    return f"{seconds // 3600:02d}:{seconds // 60 % 60:02d}:{seconds % 60:02d}"


def service_day_instant(service_date: date, seconds: int) -> datetime:
    """The UTC instant of a service-day clock time. GTFS counts from midnight of
    the service date in the feed's zone; Japan has no daylight saving."""
    midnight = datetime.combine(service_date, time(0), tzinfo=JST)
    return (midnight + timedelta(seconds=seconds)).astimezone(timezone.utc)


@dataclass(frozen=True)
class Stop:
    stop_id: str
    name: str
    lat: float
    lon: float


@dataclass(frozen=True)
class Route:
    route_id: str
    name: str
    stops: tuple[Stop, ...]


@dataclass(frozen=True)
class ScheduledTrip:
    """A timetable trip. It runs on every service day of its day type, the
    GTFS ``service_id``, and leaves each stop of its route in order at
    ``departures`` (GTFS clock strings)."""

    trip_id: str
    route_id: str
    day_type: str
    departures: tuple[str, ...]

    @property
    def scheduled_sec(self) -> tuple[int, ...]:
        return tuple(gtfs_seconds(clock) for clock in self.departures)


@dataclass(frozen=True)
class ServiceDay:
    date: date
    day_type: str


@dataclass(frozen=True)
class TripRun:
    """The truth for one trip on one service day: the actual departure from
    each stop, in seconds on the service date's clock."""

    trip_id: str
    service_date: date
    actual_sec: tuple[int, ...]

    @classmethod
    def from_delays(cls, trip: ScheduledTrip, service_date: date, delays_sec: tuple[int, ...]) -> TripRun:
        """A run that left each stop ``delays_sec`` after its scheduled
        departure; a negative delay is an early departure."""
        if len(delays_sec) != len(trip.departures):
            raise ValueError(f"{trip.trip_id}: {len(delays_sec)} delays for {len(trip.departures)} stops")
        return cls(
            trip.trip_id, service_date, tuple(s + d for s, d in zip(trip.scheduled_sec, delays_sec, strict=True))
        )


@dataclass(frozen=True)
class FeedModel:
    """How the feed reports the truth.

    Each poll carries every trip run that has entered the feed and not yet
    left its last stop. A run enters ``lead_sec`` before its first departure,
    scheduled or actual, whichever is earlier. A poll lists only the stops the
    run has not left yet.

    ``perfect``: every poll predicts each listed stop's true delay, so the last
    poll before a departure carries the true delay too.
    """

    prediction: Literal["perfect"] = "perfect"
    poll_interval_sec: int = 60
    lead_sec: int = 300


@dataclass(frozen=True)
class Scenario:
    name: str
    routes: tuple[Route, ...]
    timetable: tuple[ScheduledTrip, ...]
    days: tuple[ServiceDay, ...]
    runs: tuple[TripRun, ...]
    feed: FeedModel = field(default_factory=FeedModel)

    def __post_init__(self) -> None:
        _unique("route", [r.route_id for r in self.routes])
        _unique("trip", [t.trip_id for t in self.timetable])
        _unique("service day", [d.date for d in self.days])
        _unique("run", [(r.trip_id, r.service_date) for r in self.runs])
        stops: dict[str, Stop] = {}
        for route in self.routes:
            for stop in route.stops:
                if stops.setdefault(stop.stop_id, stop) != stop:
                    raise ValueError(f"stop {stop.stop_id} is declared twice with different details")
        for trip in self.timetable:
            route = self.route(trip.route_id)
            if len(trip.departures) != len(route.stops):
                raise ValueError(f"{trip.trip_id}: {len(trip.departures)} departures for {len(route.stops)} stops")
            _increasing(f"{trip.trip_id} schedule", trip.scheduled_sec)
        for run in self.runs:
            trip = self.trip(run.trip_id)
            if self.day_type(run.service_date) != trip.day_type:
                raise ValueError(f"{run.trip_id} runs on {run.service_date}, which is not a {trip.day_type} day")
            if len(run.actual_sec) != len(trip.departures):
                raise ValueError(f"{run.trip_id} {run.service_date}: one actual departure per stop is required")
            _increasing(f"{run.trip_id} {run.service_date} actual departures", run.actual_sec)
        ran = {(r.trip_id, r.service_date) for r in self.runs}
        for day in self.days:
            for trip in self.timetable:
                if trip.day_type == day.day_type and (trip.trip_id, day.date) not in ran:
                    raise ValueError(f"{trip.trip_id} is scheduled on {day.date} but has no run")

    def route(self, route_id: str) -> Route:
        return next(r for r in self.routes if r.route_id == route_id)

    def trip(self, trip_id: str) -> ScheduledTrip:
        return next(t for t in self.timetable if t.trip_id == trip_id)

    def day_type(self, service_date: date) -> str:
        return next(d.day_type for d in self.days if d.date == service_date)


def _unique(kind: str, keys: list) -> None:
    seen = set()
    for key in keys:
        if key in seen:
            raise ValueError(f"{kind} {key!r} is declared twice")
        seen.add(key)


def _increasing(label: str, seconds: tuple[int, ...]) -> None:
    if any(later <= earlier for earlier, later in itertools.pairwise(seconds)):
        raise ValueError(f"{label} must strictly increase: {[gtfs_clock(s) for s in seconds]}")


def _csv(header: tuple[str, ...], rows: list[tuple]) -> str:
    out = io.StringIO()
    writer = csv.writer(out, lineterminator="\n")
    writer.writerow(header)
    writer.writerows(rows)
    return out.getvalue()


def static_gtfs_zip(scenario: Scenario) -> bytes:
    """The scenario's timetable as a GTFS static zip. Service runs only on the
    declared days, through ``calendar_dates.txt``."""
    stops = {stop.stop_id: stop for route in scenario.routes for stop in route.stops}
    files = {
        "agency.txt": _csv(
            ("agency_id", "agency_name", "agency_url", "agency_timezone"),
            [("SCN", f"Scenario {scenario.name}", "https://example.com/", "Asia/Tokyo")],
        ),
        "stops.txt": _csv(
            ("stop_id", "stop_name", "stop_lat", "stop_lon"),
            [(s.stop_id, s.name, s.lat, s.lon) for s in stops.values()],
        ),
        "routes.txt": _csv(
            ("route_id", "agency_id", "route_short_name", "route_long_name", "route_type"),
            [(r.route_id, "SCN", r.route_id, r.name, 3) for r in scenario.routes],
        ),
        "trips.txt": _csv(
            ("route_id", "service_id", "trip_id", "direction_id"),
            [(t.route_id, t.day_type, t.trip_id, 0) for t in scenario.timetable],
        ),
        "stop_times.txt": _csv(
            ("trip_id", "arrival_time", "departure_time", "stop_id", "stop_sequence"),
            [
                (t.trip_id, clock, clock, stop.stop_id, seq)
                for t in scenario.timetable
                for seq, (stop, clock) in enumerate(
                    zip(scenario.route(t.route_id).stops, t.departures, strict=True), start=1
                )
            ],
        ),
        "calendar_dates.txt": _csv(
            ("service_id", "date", "exception_type"),
            [(d.day_type, d.date.strftime("%Y%m%d"), 1) for d in scenario.days],
        ),
    }
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for name, text in files.items():
            zf.writestr(name, text)
    return buf.getvalue()


@dataclass(frozen=True)
class Poll:
    """One captured TripUpdate feed."""

    captured_at: datetime
    payload: bytes
    stop_updates: int

    @property
    def source_file(self) -> str:
        """The collector's name for the capture: its UTC day and UTC time."""
        utc = self.captured_at.astimezone(timezone.utc)
        return f"{utc:%Y%m%d}/TripUpdate_{utc:%H%M%S}.pb"


def _predicted_delay(feed: FeedModel, scheduled_sec: int, actual_sec: int) -> int:
    if feed.prediction == "perfect":
        return actual_sec - scheduled_sec
    raise ValueError(f"unknown prediction model {feed.prediction!r}")


def polls(scenario: Scenario) -> list[Poll]:
    """The polls a collector captures under the scenario's feed model: one at
    every multiple of the poll interval while some trip run is in the feed.

    Raises when a departure gets no poll before it: the oracle would count a
    departure the pipeline never saw, so the scenario would be at fault, not
    the pipeline.
    """
    feed = scenario.feed
    windows = []
    for run in scenario.runs:
        trip = scenario.trip(run.trip_id)
        first = min(trip.scheduled_sec[0], run.actual_sec[0])
        enters = service_day_instant(run.service_date, first) - timedelta(seconds=feed.lead_sec)
        departs = [service_day_instant(run.service_date, s) for s in run.actual_sec]
        windows.append((run, trip, enters, departs))
    if not windows:
        return []
    step = feed.poll_interval_sec
    start = math.ceil(min(w[2] for w in windows).timestamp() / step) * step
    end = max(w[3][-1] for w in windows)
    out: list[Poll] = []
    reported: set[tuple[str, date, int]] = set()
    at = datetime.fromtimestamp(start, timezone.utc)
    while at < end:
        updates, count = [], 0
        for run, trip, enters, departs in windows:
            if not enters <= at < departs[-1]:
                continue
            stops = scenario.route(trip.route_id).stops
            listed = [
                (seq, stop, _predicted_delay(feed, scheduled, actual))
                for seq, (stop, scheduled, actual, leaves) in enumerate(
                    zip(stops, trip.scheduled_sec, run.actual_sec, departs, strict=True), start=1
                )
                if leaves > at
            ]
            reported.update((run.trip_id, run.service_date, seq) for seq, _stop, _delay in listed)
            count += len(listed)
            updates.append(
                trip_update(
                    run.trip_id,
                    *(
                        stop_time_update(seq, departure_delay=delay, stop_id=stop.stop_id)
                        for seq, stop, delay in listed
                    ),
                    route_id=trip.route_id,
                    start_date=run.service_date.strftime("%Y%m%d"),
                )
            )
        if updates:
            out.append(Poll(at, trip_update_feed(*updates, timestamp=int(at.timestamp())), count))
        at += timedelta(seconds=step)
    missed = [
        (run.trip_id, run.service_date, seq)
        for run, _trip, _enters, departs in windows
        for seq in range(1, len(departs) + 1)
        if (run.trip_id, run.service_date, seq) not in reported
    ]
    if missed:
        raise ValueError(f"no poll reports these departures before they happen: {missed}")
    return out
