"""Scenarios: a declared operation with known truth, emitted as the static
GTFS and the GTFS-RT polls a real feed would publish for it.

A scenario declares routes as ordered stops, a timetable of trips with
scheduled departures on the GTFS service-day clock (``24:10:00`` is ten past
midnight at the end of the service date), the service days it covers with the
day type each runs, and the truth: when every trip run actually left every
stop it served. A feed model turns the truth into the TripUpdate polls a
collector captures. ``tests.fixtures.scenario_oracle`` computes the expected
figures from the truth alone, so a test can push the polls through the real
ingest path and compare what the pipeline reports with what happened.

The timetable may be revised: each revision is a static GTFS version of its
own, in force from its first day, and loaded at an instant the scenario
states, as an operator loads a newly published zip.

Every scheduled trip of a declared day is accounted for in one of three ways:
it ran (a ``TripRun``, which may skip stops), it did not run and the feed
reported it cancelled (a ``Cancellation``), or it did not run and the feed
never mentioned it (an ``Unreported`` trip). A scheduled trip declared in none
of them is refused, so a forgotten run cannot pass for a cancellation.

The feed model may also misreport: a ``Misreport`` replaces a run's
predicted delays for a stretch, and a ``Freeze`` makes the feed repeat one
poll's content, its header too or not, for a stretch.

A timetable may plan a dwell, an arrival before the departure at a stop, and
a feed model may report arrivals beside departures, as the feeds confirmed to
send them do: each run then declares when it reached every stop as well.

The collector archives every poll it captures and may push the app only some
of them. Each JST calendar day of capture reaches the app by the live path,
the polls pushed, by the collector's archive, every poll, or by both in an
order the scenario states (``Delivery``).
"""

from __future__ import annotations

import csv
import io
import itertools
import math
import zipfile
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta, timezone
from typing import Literal
from zoneinfo import ZoneInfo

from tests.fixtures.gtfs_rt import stop_time_update, trip_update, trip_update_feed

JST = ZoneInfo("Asia/Tokyo")

# GTFS-RT's ScheduleRelationship values: StopTimeUpdate's SCHEDULED, SKIPPED
# and NO_DATA, and TripDescriptor's SCHEDULED and CANCELED.
SCHEDULED = 0
SKIPPED = 1
NO_DATA = 2
CANCELED = 3

# A revision is loaded at this hour of its first day unless it says otherwise:
# after the previous service day's last poll and before the day's first.
_DEFAULT_LOAD_HOUR = 3


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
    ``departures`` (GTFS clock strings). It reaches each stop at
    ``arrivals``, or at its departure when None: no dwell is planned."""

    trip_id: str
    route_id: str
    day_type: str
    departures: tuple[str, ...]
    arrivals: tuple[str, ...] | None = None

    @property
    def scheduled_sec(self) -> tuple[int, ...]:
        return tuple(gtfs_seconds(clock) for clock in self.departures)

    @property
    def scheduled_arrival_sec(self) -> tuple[int, ...]:
        return self.scheduled_sec if self.arrivals is None else tuple(gtfs_seconds(c) for c in self.arrivals)

    @classmethod
    def from_clocks(
        cls, trip_id: str, route_id: str, day_type: str, stops: tuple[tuple[str, str], ...]
    ) -> ScheduledTrip:
        """A trip planned to reach and leave each stop at the (arrival,
        departure) GTFS clocks of *stops*."""
        return cls(trip_id, route_id, day_type, tuple(d for _a, d in stops), tuple(a for a, _d in stops))


@dataclass(frozen=True)
class TimetableVersion:
    """One published static GTFS version: its trips, in force from
    ``first_day`` until the next version's first day. ``name`` is the zip's
    file stem, which the loader records as the version's name. The operator
    loads it at ``loaded_at``, by default 03:00 JST on its first day."""

    name: str
    first_day: date
    trips: tuple[ScheduledTrip, ...]
    loaded_at: datetime | None = None

    @property
    def load_instant(self) -> datetime:
        if self.loaded_at is not None:
            return self.loaded_at
        return datetime.combine(self.first_day, time(_DEFAULT_LOAD_HOUR), tzinfo=JST)


@dataclass(frozen=True)
class ServiceDay:
    date: date
    day_type: str


@dataclass(frozen=True)
class TripRun:
    """The truth for one trip on one service day: the actual departure from
    each stop, in seconds on the service date's clock, or None for a stop the
    run skipped.

    The feed reports a skipped stop SKIPPED from ``skips_announced_sec`` on
    the service date's clock, or from the run's first poll when None. Until
    then it predicts the stop like any other, at the delay of the nearest
    stop before it that the run serves (after it, when none is before).

    Under a feed model that reports arrivals, ``arrival_sec`` holds when the
    run reached each stop, on the same clock: after it left the stop before,
    and no later than it left this one. The run ends on reaching its last
    stop, so its departure there is its arrival."""

    trip_id: str
    service_date: date
    actual_sec: tuple[int | None, ...]
    skips_announced_sec: int | None = None
    arrival_sec: tuple[int, ...] | None = None

    @classmethod
    def from_delays(
        cls,
        trip: ScheduledTrip,
        service_date: date,
        delays_sec: tuple[int | None, ...],
        *,
        skips_announced_sec: int | None = None,
    ) -> TripRun:
        """A run that left each stop ``delays_sec`` after its scheduled
        departure; a negative delay is an early departure and None a skipped
        stop."""
        if len(delays_sec) != len(trip.departures):
            raise ValueError(f"{trip.trip_id}: {len(delays_sec)} delays for {len(trip.departures)} stops")
        return cls(
            trip.trip_id,
            service_date,
            tuple(None if d is None else s + d for s, d in zip(trip.scheduled_sec, delays_sec, strict=True)),
            skips_announced_sec,
        )

    @classmethod
    def from_clocks(cls, trip: ScheduledTrip, service_date: date, stops: tuple[tuple[str, str], ...]) -> TripRun:
        """A run, for a feed that reports arrivals, that reached and left each
        stop at the (arrival, departure) clocks of *stops*."""
        if len(stops) != len(trip.departures):
            raise ValueError(f"{trip.trip_id}: {len(stops)} stops' clocks for {len(trip.departures)} stops")
        return cls(
            trip.trip_id,
            service_date,
            tuple(gtfs_seconds(d) for _a, d in stops),
            arrival_sec=tuple(gtfs_seconds(a) for a, _d in stops),
        )

    @property
    def served(self) -> tuple[int, ...]:
        """The actual departures of the stops the run served, in order."""
        return tuple(sec for sec in self.actual_sec if sec is not None)


@dataclass(frozen=True)
class Cancellation:
    """A scheduled trip that did not run on a service day, which the feed
    reported as not running.

    The trip is in the feed from the feed model's lead before its first
    scheduled departure until its last. Until ``announced_sec`` on the
    service date's clock (from the start when None) the feed predicts every
    stop ``predicted_delay_sec`` late, as for a trip about to run. From then
    on it reports the trip in one of the forms feeds use, ``reported_as``:

    - ``canceled``: the trip CANCELED, each stop not yet due listed with NO_DATA;
    - ``canceled_without_stops``: the trip CANCELED with no stop listed, which
      GTFS-RT allows for a cancelled trip;
    - ``every_stop_skipped``: the trip SCHEDULED, each stop not yet due listed
      SKIPPED;
    - ``skipped_then_no_data``: the trip SCHEDULED and every stop listed,
      SKIPPED until it is due and NO_DATA after, so each stop's last report
      but the final stop's carries neither a delay nor a skip.
    """

    trip_id: str
    service_date: date
    announced_sec: int | None = None
    reported_as: Literal["canceled", "canceled_without_stops", "every_stop_skipped", "skipped_then_no_data"] = (
        "canceled"
    )
    predicted_delay_sec: int = 0


@dataclass(frozen=True)
class Unreported:
    """A scheduled trip that did not run on a service day, and that the feed
    never mentioned."""

    trip_id: str
    service_date: date


class _Stretch:
    """A stretch of ``service_date``'s clock, from ``from_sec`` until ``until_sec``."""

    service_date: date
    from_sec: int
    until_sec: int

    @property
    def starts(self) -> datetime:
        return service_day_instant(self.service_date, self.from_sec)

    @property
    def ends(self) -> datetime:
        return service_day_instant(self.service_date, self.until_sec)

    def covers(self, at: datetime) -> bool:
        return self.starts <= at < self.ends


@dataclass(frozen=True)
class Misreport(_Stretch):
    """A stretch in which the feed reports one run's stops at a delay other
    than its prediction: every poll captured from ``from_sec`` until
    ``until_sec`` on the service date's clock lists each of ``stops`` (stop
    sequences; every stop it lists when empty) at ``delay_sec``. A run that
    skips a stop cannot be misreported."""

    trip_id: str
    service_date: date
    delay_sec: int
    from_sec: int
    until_sec: int
    stops: tuple[int, ...] = ()

    def replaces(self, seq: int, at: datetime) -> bool:
        """Whether a poll captured *at* lists stop *seq* at the misreported delay."""
        return self.covers(at) and (not self.stops or seq in self.stops)


@dataclass(frozen=True)
class Freeze(_Stretch):
    """A stretch in which the feed stops updating. The first poll captured
    from ``from_sec`` on ``service_date``'s clock is made as usual, and every
    later poll captured before ``until_sec`` repeats its TripUpdates. With
    ``header`` ``frozen`` they repeat its FeedHeader timestamp too, so their
    bytes are the first poll's; with ``advancing`` each carries the instant
    it was captured, as a feed whose predictions stop moving while its
    header advances. A scenario with a freeze may neither cancel a trip nor
    skip a stop."""

    service_date: date
    from_sec: int
    until_sec: int
    header: Literal["frozen", "advancing"] = "frozen"


@dataclass(frozen=True)
class FeedModel:
    """How the feed reports the truth.

    Each poll carries every trip run that has entered the feed and not yet
    left its last served stop. A run enters ``lead_sec`` before its first
    departure, scheduled or actual, whichever is earlier. A poll lists only
    the stops the run has not left yet; a skipped stop stays listed until the
    run leaves the next stop it serves.

    Every TripUpdate and StopTimeUpdate carries its schedule relationship,
    SCHEDULED unless the truth says otherwise, as the confirmed feeds do.
    Each poll's FeedHeader timestamp is the instant it was captured, unless a
    freeze holds it.

    ``perfect``: every poll predicts each listed stop's true delay, so the last
    poll before a departure carries the true delay too, except where a
    ``misreports`` stretch replaces it. ``freezes`` repeat a poll's content
    over a stretch, so a stop it lists stays listed, at the delay it had,
    after the run has left it.

    ``arrivals``: the feed reports arrivals too, as the feeds confirmed to
    send them do, each stop update carrying an arrival or a departure, never
    both, and a run's last stop only as an arrival. The feed knows when the
    run will reach each stop but not how long it will stand there. A stop
    the run has not reached yet is listed with its departure predicted at
    the delay of its arrival, as if the run kept the planned dwell, and the
    last stop with its true arrival delay. Of the polls captured while the
    run stands at a stop, the first lists the stop with its arrival and each
    later one with its true departure delay, so a dwell no poll falls in
    leaves the arrival unreported, and a dwell only one poll falls in leaves
    the departure reported only before the arrival. The run leaves the feed
    on reaching its last stop. Such a feed neither misreports nor freezes,
    and runs every trip it schedules: none is cancelled or goes unreported,
    and no run skips a stop.

    ``push_interval_sec``: the collector polls every ``poll_interval_sec``
    and archives every poll, but pushes the app one poll every
    ``push_interval_sec``, a whole multiple of the poll interval, or every
    poll when None. The collector pushes every n-th poll it makes; a poll
    is pushed here when its capture instant is a whole multiple of the push
    interval on the POSIX clock, which fixes the phase so a scenario can
    tell which polls are pushed. A feed pushed more sparsely than it is
    polled neither cancels a trip nor skips a stop, since the polls it
    pushes might not carry the report.
    """

    prediction: Literal["perfect"] = "perfect"
    poll_interval_sec: int = 60
    lead_sec: int = 300
    misreports: tuple[Misreport, ...] = ()
    freezes: tuple[Freeze, ...] = ()
    arrivals: bool = False
    push_interval_sec: int | None = None

    @property
    def sparse(self) -> bool:
        """Whether the collector pushes fewer polls than it captures."""
        return self.push_interval_sec is not None and self.push_interval_sec != self.poll_interval_sec

    def pushes(self, poll: Poll) -> bool:
        """Whether the collector pushes *poll* to the app."""
        return self.push_interval_sec is None or int(poll.captured_at.timestamp()) % self.push_interval_sec == 0


@dataclass(frozen=True)
class Delivery:
    """How the polls captured on one JST calendar day ``day`` reach the app.

    ``live``: the collector pushes them as it captures them (``FeedModel``),
    and promotion copies the day into history once it has closed.
    ``archive``: the operator ingests the collector's archive of the day,
    which holds every poll it captured. A day offered both ways has its
    archive ingested after promotion has run, as the daily archive job
    follows the promotion run, unless ``archive_first``: the archive is
    ingested before promotion reaches the day, as a backfill can be. A day
    no ``Delivery`` names reaches the app by the live path alone."""

    day: date
    live: bool = True
    archive: bool = False
    archive_first: bool = False


@dataclass(frozen=True)
class Scenario:
    """An operation and its truth. ``timetable`` is in force from the first
    service day; each of ``revisions`` replaces it from its own first day.
    ``deliveries`` says how the polls of each JST calendar day of capture
    reach the app. A scenario that declares a delivery runs one timetable,
    so that every day's polls are taken in the one order ``handoff`` gives,
    and one whose archive is ingested freezes no feed, since archive ingest
    stamps a poll with its FeedHeader timestamp, not its capture instant."""

    name: str
    routes: tuple[Route, ...]
    timetable: tuple[ScheduledTrip, ...]
    days: tuple[ServiceDay, ...]
    runs: tuple[TripRun, ...]
    feed: FeedModel = field(default_factory=FeedModel)
    revisions: tuple[TimetableVersion, ...] = ()
    cancellations: tuple[Cancellation, ...] = ()
    unreported: tuple[Unreported, ...] = ()
    deliveries: tuple[Delivery, ...] = ()

    def __post_init__(self) -> None:
        self._check_deliveries()
        events: list[TripRun | Cancellation | Unreported] = [*self.runs, *self.cancellations, *self.unreported]
        _unique("route", [r.route_id for r in self.routes])
        _unique("service day", [d.date for d in self.days])
        _unique("timetable version", [v.name for v in self.versions])
        _unique("run, cancellation or unreported trip", [(e.trip_id, e.service_date) for e in events])
        stops: dict[str, Stop] = {}
        for route in self.routes:
            for stop in route.stops:
                if stops.setdefault(stop.stop_id, stop) != stop:
                    raise ValueError(f"stop {stop.stop_id} is declared twice with different details")
        first_days = [v.first_day for v in self.versions]
        if any(later <= earlier for earlier, later in itertools.pairwise(first_days)):
            raise ValueError(f"each revision must start after the previous timetable: {first_days}")
        for version in self.versions:
            _unique(f"{version.name} trip", [t.trip_id for t in version.trips])
            for trip in version.trips:
                route = self.route(trip.route_id)
                if len(trip.departures) != len(route.stops):
                    raise ValueError(f"{trip.trip_id}: {len(trip.departures)} departures for {len(route.stops)} stops")
                _increasing(f"{trip.trip_id} schedule", trip.scheduled_sec)
                if len(trip.scheduled_arrival_sec) != len(trip.departures):
                    raise ValueError(f"{trip.trip_id}: one scheduled arrival per stop is required")
                _visits_in_order(f"{trip.trip_id} schedule", trip.scheduled_arrival_sec, trip.scheduled_sec)
        for event in events:
            trip = self.trip(event.trip_id, event.service_date)
            if self.day_type(event.service_date) != trip.day_type:
                raise ValueError(f"{event.trip_id} runs on {event.service_date}, which is not a {trip.day_type} day")
        for run in self.runs:
            trip = self.trip(run.trip_id, run.service_date)
            if len(run.actual_sec) != len(trip.departures):
                raise ValueError(f"{run.trip_id} {run.service_date}: one actual departure per stop is required")
            if not run.served:
                raise ValueError(f"{run.trip_id} {run.service_date} serves no stop; declare a cancellation")
            if run.skips_announced_sec is not None and len(run.served) == len(run.actual_sec):
                raise ValueError(f"{run.trip_id} {run.service_date} announces skips but skips no stop")
            _increasing(f"{run.trip_id} {run.service_date} actual departures", run.served)
            if (run.arrival_sec is not None) != self.feed.arrivals:
                raise ValueError(
                    f"{run.trip_id} {run.service_date}: a run declares its arrivals exactly when the feed reports them"
                )
            if run.arrival_sec is not None:
                if len(run.served) != len(run.actual_sec):
                    raise ValueError(f"{run.trip_id} {run.service_date}: a feed that reports arrivals skips no stop")
                if len(run.arrival_sec) != len(run.actual_sec):
                    raise ValueError(f"{run.trip_id} {run.service_date}: one actual arrival per stop is required")
                _visits_in_order(f"{run.trip_id} {run.service_date}", run.arrival_sec, run.served)
                if run.arrival_sec[-1] != run.served[-1]:
                    raise ValueError(f"{run.trip_id} {run.service_date}: a run ends on reaching its last stop")
        if self.feed.arrivals and (self.feed.misreports or self.feed.freezes):
            raise ValueError("a feed that reports arrivals neither misreports nor freezes")
        if self.feed.arrivals and (self.cancellations or self.unreported):
            raise ValueError("a feed that reports arrivals runs every trip it schedules")
        for cancellation in self.cancellations:
            trip = self.trip(cancellation.trip_id, cancellation.service_date)
            first_predicted = trip.scheduled_sec[0] + cancellation.predicted_delay_sec
            if cancellation.announced_sec is not None and cancellation.announced_sec >= first_predicted:
                raise ValueError(
                    f"{cancellation.trip_id}: a cancellation must be announced before the trip is due to leave"
                )
        runs = {(r.trip_id, r.service_date): r for r in self.runs}
        for misreport in self.feed.misreports:
            run = runs.get((misreport.trip_id, misreport.service_date))
            if run is None:
                raise ValueError(f"{misreport.trip_id} {misreport.service_date}: only a run can be misreported")
            if len(run.served) != len(run.actual_sec):
                raise ValueError(f"{misreport.trip_id} {misreport.service_date} skips a stop and cannot be misreported")
            if misreport.ends <= misreport.starts:
                raise ValueError(f"{misreport.trip_id}: a misreport must end after it starts")
            if any(not 1 <= seq <= len(run.actual_sec) for seq in misreport.stops):
                raise ValueError(f"{misreport.trip_id}: a misreport names a stop the trip does not have")
        freezes = sorted(self.feed.freezes, key=lambda f: f.starts)
        for freeze in freezes:
            if freeze.ends <= freeze.starts:
                raise ValueError(f"a freeze on {freeze.service_date} must end after it starts")
        if any(later.starts < earlier.ends for earlier, later in itertools.pairwise(freezes)):
            raise ValueError("freezes must not overlap")
        if freezes and (self.cancellations or any(len(r.served) != len(r.actual_sec) for r in self.runs)):
            raise ValueError("a scenario with a freeze may neither cancel a trip nor skip a stop")
        accounted = {(e.trip_id, e.service_date) for e in events}
        for day in self.days:
            for trip in self.scheduled(day.date):
                if (trip.trip_id, day.date) not in accounted:
                    raise ValueError(
                        f"{trip.trip_id} is scheduled on {day.date} but has no run, cancellation or unreported entry"
                    )

    def _check_deliveries(self) -> None:
        feed = self.feed
        _unique("delivery day", [d.day for d in self.deliveries])
        for delivery in self.deliveries:
            if not (delivery.live or delivery.archive):
                raise ValueError(f"{delivery.day} reaches the app by no path")
            if delivery.archive_first and not (delivery.live and delivery.archive):
                raise ValueError(f"{delivery.day}: only a day offered both ways is archived first")
        if feed.push_interval_sec is not None and feed.push_interval_sec <= 0:
            raise ValueError("a push interval must be positive")
        if feed.push_interval_sec is not None and feed.push_interval_sec % feed.poll_interval_sec:
            raise ValueError(f"a push every {feed.push_interval_sec} s is not a multiple of the poll interval")
        if feed.sparse and (self.cancellations or any(None in r.actual_sec for r in self.runs)):
            raise ValueError("a feed pushed more sparsely than it is polled neither cancels a trip nor skips a stop")
        if self.deliveries and self.revisions:
            raise ValueError("a scenario that declares a delivery runs one timetable")
        if any(d.archive for d in self.deliveries) and feed.freezes:
            raise ValueError("a scenario whose archive is ingested freezes no feed")

    def delivery(self, day: date) -> Delivery:
        """How the polls captured on JST calendar *day* reach the app."""
        return next((d for d in self.deliveries if d.day == day), Delivery(day))

    @property
    def versions(self) -> tuple[TimetableVersion, ...]:
        """Every timetable version in force order; the first is ``timetable``,
        named after the scenario."""
        first = min(d.date for d in self.days)
        return (TimetableVersion(f"scenario_{self.name}", first, self.timetable), *self.revisions)

    def version_on(self, service_date: date) -> TimetableVersion:
        """The timetable version in force on *service_date*."""
        return [v for v in self.versions if v.first_day <= service_date][-1]

    def scheduled(self, service_date: date) -> tuple[ScheduledTrip, ...]:
        """The trips the timetable in force schedules on *service_date*."""
        day_type = self.day_type(service_date)
        return tuple(t for t in self.version_on(service_date).trips if t.day_type == day_type)

    def route(self, route_id: str) -> Route:
        return next(r for r in self.routes if r.route_id == route_id)

    def trip(self, trip_id: str, service_date: date) -> ScheduledTrip:
        """The trip as the timetable in force on *service_date* schedules it."""
        version = self.version_on(service_date)
        found = [t for t in version.trips if t.trip_id == trip_id]
        if not found:
            raise ValueError(f"{trip_id} is not in {version.name}, in force on {service_date}")
        return found[0]

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


def _visits_in_order(label: str, arrivals: tuple[int, ...], departures: tuple[int, ...]) -> None:
    """Refuse an arrival at a stop after its departure, or not after the
    departure from the stop before."""
    for i, (arrival, departure) in enumerate(zip(arrivals, departures, strict=True)):
        if arrival > departure:
            raise ValueError(
                f"{label}: stop {i + 1} is reached at {gtfs_clock(arrival)}, "
                f"after it is left at {gtfs_clock(departure)}"
            )
        if i and arrival <= departures[i - 1]:
            raise ValueError(
                f"{label}: stop {i + 1} is reached at {gtfs_clock(arrival)}, "
                f"not after stop {i} is left at {gtfs_clock(departures[i - 1])}"
            )


def _csv(header: tuple[str, ...], rows: list[tuple]) -> str:
    out = io.StringIO()
    writer = csv.writer(out, lineterminator="\n")
    writer.writerow(header)
    writer.writerows(rows)
    return out.getvalue()


def static_gtfs_zip(scenario: Scenario, version: TimetableVersion | None = None) -> bytes:
    """A timetable version, the first when None, as a GTFS static zip.
    Service runs only on the declared days the version is in force on,
    through ``calendar_dates.txt``. Every version names every route."""
    version = version or scenario.versions[0]
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
            [(t.route_id, t.day_type, t.trip_id, 0) for t in version.trips],
        ),
        "stop_times.txt": _csv(
            ("trip_id", "arrival_time", "departure_time", "stop_id", "stop_sequence"),
            [
                (t.trip_id, arrival, departure, stop.stop_id, seq)
                for t in version.trips
                for seq, (stop, arrival, departure) in enumerate(
                    zip(scenario.route(t.route_id).stops, t.arrivals or t.departures, t.departures, strict=True),
                    start=1,
                )
            ],
        ),
        "calendar_dates.txt": _csv(
            ("service_id", "date", "exception_type"),
            [
                (d.day_type, d.date.strftime("%Y%m%d"), 1)
                for d in scenario.days
                if scenario.version_on(d.date) == version
            ],
        ),
    }
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for name, text in files.items():
            zf.writestr(name, text)
    return buf.getvalue()


@dataclass(frozen=True)
class StopEntry:
    """One StopTimeUpdate of a poll: its stop, the departure delay it
    predicts (None for none), its schedule relationship, and the arrival
    delay it reports (None for none)."""

    stop_sequence: int
    stop_id: str
    departure_delay: int | None
    status: int
    arrival_delay: int | None = None


@dataclass(frozen=True)
class TripEntry:
    """One TripUpdate of a poll: its trip of one service day, the trip's
    schedule relationship, the stops it lists, and how many of the stops the
    timetable schedules the trip at are not due yet when the poll is
    captured."""

    trip_id: str
    route_id: str
    service_date: date
    status: int
    stops: tuple[StopEntry, ...]
    stops_to_come: int


@dataclass(frozen=True)
class Poll:
    """One captured TripUpdate feed: what it lists, its FeedHeader timestamp
    (the capture instant when None), and its wire bytes."""

    captured_at: datetime
    trips: tuple[TripEntry, ...]
    header_at: datetime | None = None

    @property
    def header(self) -> datetime:
        return self.captured_at if self.header_at is None else self.header_at

    @property
    def service_dates(self) -> frozenset[date]:
        return frozenset(t.service_date for t in self.trips)

    @property
    def stop_updates(self) -> int:
        return sum(len(t.stops) for t in self.trips)

    @property
    def stored_rows(self) -> int:
        """The rows ingest stores for the poll: one per stop update, and for
        each trip it reports CANCELED without listing a stop, one per stop not
        due yet, or the one trip-level row when none is."""
        return self.stop_updates + sum(t.stops_to_come or 1 for t in self.trips if t.status == CANCELED and not t.stops)

    @property
    def payload(self) -> bytes:
        return trip_update_feed(
            *(
                trip_update(
                    t.trip_id,
                    *(
                        stop_time_update(
                            s.stop_sequence,
                            departure_delay=s.departure_delay,
                            arrival_delay=s.arrival_delay,
                            stop_id=s.stop_id,
                            schedule_relationship=s.status,
                        )
                        for s in t.stops
                    ),
                    route_id=t.route_id,
                    start_date=t.service_date.strftime("%Y%m%d"),
                    schedule_relationship=t.status,
                )
                for t in self.trips
            ),
            timestamp=int(self.header.timestamp()),
        )

    @property
    def source_file(self) -> str:
        """The collector's name for the capture: its UTC day and UTC time."""
        utc = self.captured_at.astimezone(timezone.utc)
        return f"{utc:%Y%m%d}/TripUpdate_{utc:%H%M%S}.pb"


def _predicted_delay(feed: FeedModel, scheduled_sec: int, actual_sec: int) -> int:
    if feed.prediction == "perfect":
        return actual_sec - scheduled_sec
    raise ValueError(f"unknown prediction model {feed.prediction!r}")


@dataclass(frozen=True)
class _InFeed:
    """One trip of one service day while it is in the feed, and what each
    poll inside its window lists for it: the trip's status and its stops."""

    trip: ScheduledTrip
    service_date: date
    enters: datetime
    leaves: datetime
    listing: Callable[[datetime], tuple[int, tuple[StopEntry, ...]]]


def _run_listing(scenario: Scenario, run: TripRun, trip: ScheduledTrip) -> _InFeed:
    feed = scenario.feed
    stops = scenario.route(trip.route_id).stops
    served = {i: sec for i, sec in enumerate(run.actual_sec) if sec is not None}
    announced = (
        None if run.skips_announced_sec is None else service_day_instant(run.service_date, run.skips_announced_sec)
    )
    misreports = [m for m in feed.misreports if (m.trip_id, m.service_date) == (run.trip_id, run.service_date)]

    def reported_delay(seq: int, predicted: int, at: datetime) -> int:
        return next((m.delay_sec for m in misreports if m.replaces(seq, at)), predicted)

    def passes(i: int) -> datetime:
        later = [j for j in served if j > i]
        return service_day_instant(run.service_date, served[later[0] if later else max(served)])

    def stand_in_delay(i: int) -> int:
        nearest = [j for j in served if j < i][-1:] or [j for j in served if j > i][:1]
        return _predicted_delay(feed, trip.scheduled_sec[nearest[0]], served[nearest[0]])

    first = min(trip.scheduled_sec[0], run.served[0])
    enters = service_day_instant(run.service_date, first) - timedelta(seconds=feed.lead_sec)
    leaves = service_day_instant(run.service_date, run.served[-1])

    def listing(at: datetime) -> tuple[int, tuple[StopEntry, ...]]:
        if feed.arrivals:
            return SCHEDULED, _with_arrivals(scenario, run, trip, enters, at)
        entries = []
        for i, (stop, scheduled, actual) in enumerate(zip(stops, trip.scheduled_sec, run.actual_sec, strict=True)):
            if actual is not None:
                if service_day_instant(run.service_date, actual) > at:
                    delay = reported_delay(i + 1, _predicted_delay(feed, scheduled, actual), at)
                    entries.append(StopEntry(i + 1, stop.stop_id, delay, SCHEDULED))
            elif passes(i) > at:
                if announced is None or at >= announced:
                    entries.append(StopEntry(i + 1, stop.stop_id, None, SKIPPED))
                else:
                    entries.append(StopEntry(i + 1, stop.stop_id, stand_in_delay(i), SCHEDULED))
        return SCHEDULED, tuple(entries)

    return _InFeed(trip, run.service_date, enters, leaves, listing)


def _with_arrivals(
    scenario: Scenario, run: TripRun, trip: ScheduledTrip, enters: datetime, at: datetime
) -> tuple[StopEntry, ...]:
    """The stops a poll captured *at* lists for *run*, which entered the feed
    at *enters*, under a feed that reports arrivals (``FeedModel``)."""
    if run.arrival_sec is None:
        raise ValueError(f"{run.trip_id} {run.service_date} declares no arrivals")
    step = timedelta(seconds=scenario.feed.poll_interval_sec)
    stops = scenario.route(trip.route_id).stops
    entries = []
    for i, (stop, departed) in enumerate(zip(stops, run.served, strict=True)):
        reached = service_day_instant(run.service_date, run.arrival_sec[i])
        arrival_delay = run.arrival_sec[i] - trip.scheduled_arrival_sec[i]
        if i == len(stops) - 1:
            if at < reached:
                entries.append(StopEntry(i + 1, stop.stop_id, None, SCHEDULED, arrival_delay))
        elif at < reached:
            entries.append(StopEntry(i + 1, stop.stop_id, arrival_delay, SCHEDULED))
        elif at < service_day_instant(run.service_date, departed):
            if at - step < max(reached, enters):
                entries.append(StopEntry(i + 1, stop.stop_id, None, SCHEDULED, arrival_delay))
            else:
                entries.append(StopEntry(i + 1, stop.stop_id, departed - trip.scheduled_sec[i], SCHEDULED))
    return tuple(entries)


def _cancellation_listing(scenario: Scenario, cancellation: Cancellation, trip: ScheduledTrip) -> _InFeed:
    feed = scenario.feed
    day = cancellation.service_date
    stops = scenario.route(trip.route_id).stops
    announced = None if cancellation.announced_sec is None else service_day_instant(day, cancellation.announced_sec)

    def listing(at: datetime) -> tuple[int, tuple[StopEntry, ...]]:
        if announced is not None and at < announced:
            delay = cancellation.predicted_delay_sec
            return SCHEDULED, tuple(
                StopEntry(seq, stop.stop_id, delay, SCHEDULED)
                for seq, (stop, scheduled) in enumerate(zip(stops, trip.scheduled_sec, strict=True), start=1)
                if service_day_instant(day, scheduled + delay) > at
            )
        if cancellation.reported_as == "canceled_without_stops":
            return CANCELED, ()
        if cancellation.reported_as == "skipped_then_no_data":
            return SCHEDULED, tuple(
                StopEntry(seq, stop.stop_id, None, SKIPPED if service_day_instant(day, scheduled) > at else NO_DATA)
                for seq, (stop, scheduled) in enumerate(zip(stops, trip.scheduled_sec, strict=True), start=1)
            )
        trip_status, stop_status = (
            (SCHEDULED, SKIPPED) if cancellation.reported_as == "every_stop_skipped" else (CANCELED, NO_DATA)
        )
        return trip_status, tuple(
            StopEntry(seq, stop.stop_id, None, stop_status)
            for seq, (stop, scheduled) in enumerate(zip(stops, trip.scheduled_sec, strict=True), start=1)
            if service_day_instant(day, scheduled) > at
        )

    enters = service_day_instant(day, trip.scheduled_sec[0]) - timedelta(seconds=feed.lead_sec)
    leaves = service_day_instant(day, trip.scheduled_sec[-1])
    return _InFeed(trip, day, enters, leaves, listing)


def _reports(entry: TripEntry) -> set[tuple[str, str, date, int]]:
    """What *entry* tells the collector: each departure and arrival it
    reports, each stop it reports SKIPPED, and a trip reported not running,
    CANCELED or with every stop it lists SKIPPED."""
    out = set()
    for stop in entry.stops:
        if stop.status == SKIPPED:
            out.add(("skip", entry.trip_id, entry.service_date, stop.stop_sequence))
        elif stop.departure_delay is not None:
            out.add(("departure", entry.trip_id, entry.service_date, stop.stop_sequence))
        elif stop.arrival_delay is not None:
            out.add(("arrival", entry.trip_id, entry.service_date, stop.stop_sequence))
    if entry.status == CANCELED or (entry.stops and all(s.status == SKIPPED for s in entry.stops)):
        out.add(("not run", entry.trip_id, entry.service_date, 0))
    return out


def _fresh_poll(in_feed: list[_InFeed], at: datetime) -> Poll:
    trips = []
    for item in in_feed:
        if item.enters <= at < item.leaves:
            status, stops = item.listing(at)
            trips.append(
                TripEntry(
                    item.trip.trip_id,
                    item.trip.route_id,
                    item.service_date,
                    status,
                    stops,
                    sum(1 for sec in item.trip.scheduled_sec if service_day_instant(item.service_date, sec) >= at),
                )
            )
    return Poll(at, tuple(trips))


def _misreported(scenario: Scenario, runs: dict[tuple[str, date], TripRun], poll: Poll) -> set[Misreport]:
    """The misreports *poll* carries: those whose run it lists a stop of at
    the misreported delay while the stretch is on, where the feed would
    otherwise have sent another."""
    out = set()
    for m in scenario.feed.misreports:
        run = runs[(m.trip_id, m.service_date)]
        scheduled = scenario.trip(run.trip_id, run.service_date).scheduled_sec
        for entry in poll.trips:
            if (entry.trip_id, entry.service_date) != (m.trip_id, m.service_date):
                continue
            for s in entry.stops:
                actual = run.actual_sec[s.stop_sequence - 1]
                carried = (
                    actual is not None
                    and s.departure_delay == m.delay_sec
                    and m.replaces(s.stop_sequence, poll.captured_at)
                    and m.delay_sec != _predicted_delay(scenario.feed, scheduled[s.stop_sequence - 1], actual)
                )
                if carried:
                    out.add(m)
    return out


def _expected_report(scenario: Scenario, run: TripRun, i: int) -> str:
    """What the feed must report of stop *i* (from 0) of *run* before the run gets there."""
    if run.actual_sec[i] is None:
        return "skip"
    return "arrival" if scenario.feed.arrivals and i == len(run.actual_sec) - 1 else "departure"


def polls(scenario: Scenario) -> list[Poll]:
    """The polls a collector captures under the scenario's feed model: one at
    every multiple of the poll interval while some trip is in the feed. A poll
    inside a freeze after its first repeats that first poll's TripUpdates,
    and its header too when the freeze holds it.

    Raises when a departure, or the arrival at a run's last stop under a
    feed that reports arrivals, gets no poll before it, a skipped stop is
    never reported SKIPPED, or a trip that did not run is never reported so:
    the oracle would count something the pipeline never saw, so the scenario
    would be at fault, not the pipeline. Raises too when no poll carries a
    misreport, which would then test nothing.
    """
    in_feed = [_run_listing(scenario, r, scenario.trip(r.trip_id, r.service_date)) for r in scenario.runs]
    in_feed += [
        _cancellation_listing(scenario, c, scenario.trip(c.trip_id, c.service_date)) for c in scenario.cancellations
    ]
    if not in_feed:
        return []
    step = scenario.feed.poll_interval_sec
    start = math.ceil(min(w.enters for w in in_feed).timestamp() / step) * step
    end = max(w.leaves for w in in_feed)
    out: list[Poll] = []
    reported: set[tuple[str, str, date, int]] = set()
    carried: set[Misreport] = set()
    runs = {(r.trip_id, r.service_date): r for r in scenario.runs}
    held: dict[Freeze, Poll] = {}
    at = datetime.fromtimestamp(start, timezone.utc)
    while at < end:
        freeze = next((f for f in scenario.feed.freezes if f.covers(at)), None)
        first = None if freeze is None else held.get(freeze)
        if freeze is None or first is None:
            poll = _fresh_poll(in_feed, at)
            if freeze is not None:
                held[freeze] = poll
        else:
            poll = Poll(at, first.trips, first.header if freeze.header == "frozen" else None)
        for entry in poll.trips:
            reported |= _reports(entry)
        carried |= _misreported(scenario, runs, poll)
        if poll.trips:
            out.append(poll)
        at += timedelta(seconds=step)
    expected = {
        (_expected_report(scenario, run, i), run.trip_id, run.service_date, i + 1)
        for run in scenario.runs
        for i in range(len(run.actual_sec))
    }
    expected |= {("not run", c.trip_id, c.service_date, 0) for c in scenario.cancellations}
    missed = sorted(expected - reported, key=str)
    if missed:
        raise ValueError(f"no poll reports these before they happen: {missed}")
    idle = [m for m in scenario.feed.misreports if m not in carried]
    if idle:
        raise ValueError(f"no poll carries these misreports: {idle}")
    return out


def poll_batches(scenario: Scenario) -> list[tuple[TimetableVersion, list[Poll]]]:
    """The polls split by the timetable loaded when each was captured: each
    version with the polls from its load until the next version's.

    Raises when a poll carries a trip of a day whose timetable is not loaded
    yet, since its stops could only be joined to an older timetable."""
    versions = scenario.versions
    loads = [v.load_instant for v in versions[1:]]
    if any(later <= earlier for earlier, later in itertools.pairwise(loads)):
        raise ValueError(f"timetable versions must be loaded in order: {loads}")
    batches: list[tuple[TimetableVersion, list[Poll]]] = [(v, []) for v in versions]
    for poll in polls(scenario):
        index = sum(1 for load in loads if load <= poll.captured_at)
        for day in poll.service_dates:
            if versions.index(scenario.version_on(day)) > index:
                raise ValueError(f"{poll.source_file} carries {day} before its timetable is loaded")
        batches[index][1].append(poll)
    return batches


def capture_day(poll: Poll) -> date:
    """The JST calendar day *poll* was captured on."""
    return poll.captured_at.astimezone(JST).date()


@dataclass(frozen=True)
class Handoff:
    """What the app is handed of some captured polls, by the step that hands
    it over, in the order the operator takes the steps: the archive ingested
    before promotion runs, the polls the collector pushed, which promotion
    copies into history, and the archive ingested after promotion."""

    archived_first: tuple[Poll, ...]
    pushed: tuple[Poll, ...]
    archived_after: tuple[Poll, ...]


def handoff(scenario: Scenario, captured: Sequence[Poll]) -> Handoff:
    """*captured* as each day's ``Delivery`` hands it to the app.

    Raises when a delivery names a day no poll of *captured* was captured
    on, which would then test nothing."""
    days = {capture_day(poll) for poll in captured}
    idle = sorted(d.day for d in scenario.deliveries if d.day not in days)
    if idle:
        raise ValueError(f"no poll is captured on these delivery days: {idle}")
    first: list[Poll] = []
    pushed: list[Poll] = []
    after: list[Poll] = []
    for poll in captured:
        delivery = scenario.delivery(capture_day(poll))
        if delivery.live and scenario.feed.pushes(poll):
            pushed.append(poll)
        if delivery.archive:
            (first if delivery.archive_first else after).append(poll)
    return Handoff(tuple(first), tuple(pushed), tuple(after))
