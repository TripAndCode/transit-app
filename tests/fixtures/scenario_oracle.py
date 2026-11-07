"""Expected delay figures computed from a scenario's declared truth alone.

Nothing here imports pipeline or api code or their constants. Each definition
below is this oracle's own statement of what a figure means, so where the
pipeline disagrees, either the pipeline is wrong or it differs on purpose; the
scenario test says which.

Semantics assumed:

- A departure is one trip run leaving one stop: a (trip, service date, stop)
  triple. Every stop a run served is a departure, its last included.
- A stop a run skipped is no departure, and a trip that did not run, whether
  cancelled or never reported, has none: none of them has a delay, so none
  counts in any delay figure. A prediction the feed later withdraws, for a
  stop it then reports SKIPPED or a trip it then reports CANCELED, is not a
  departure either.
- Its delay is the actual departure minus the scheduled departure, in whole
  seconds. Early is negative. Its schedule is the timetable version in force
  on its service date.
- A departure belongs to its trip's service date, whichever calendar day it
  falls on: a 24:10:00 departure is part of the service day that began the
  previous morning.
- A departure counts once, however many polls reported it. Under the
  ``perfect`` feed model every poll already carries the true delay, so the
  sample counts are the only figures that exercise deduplication across polls.
- On time (the legacy rule): left early by any amount, or at most
  ``ON_TIME_MAX_LATE_SEC`` late.
- On time (the industry window): at most ``EARLY_SEC`` early and at most
  ``OVER_FIVE_MIN_SEC`` late.
- Early: more than ``EARLY_SEC`` early.
- Over five minutes late: more than ``OVER_FIVE_MIN_SEC`` late.
- Mean delay: the plain mean over departures, early ones counting negative.
- A departure's hour is the whole hour of its scheduled departure on the
  service-day clock: 07:58:00 is hour 7, and 24:10:00 is hour 24 of the
  service day its trip belongs to. Its weekday is its service date's ISO
  weekday, Monday 1.

Service delivered, per service date, for a feed confirmed to send the trip
and stop statuses:

- Planned: the trips the timetable version in force that day schedules for
  its day type.
- Reported not run: the planned trips the feed reported as not running,
  either CANCELED (whether its update listed the trip's stops or none) or
  with every stop it reports SKIPPED. A run that skipped only some stops ran:
  a partial trip is still delivered.
- Executed: planned minus reported not run. A planned trip the feed never
  mentions counts as executed, since silence is not evidence that a trip did
  not run; observation coverage measures that silence instead.
- Observed: the planned trips the feed reported a departure for. Under the
  ``perfect`` feed model that is every run, since a run serves at least one
  stop and the feed reports each departure before it happens.
- The service-delivered rate, executed over planned, is shown only once the
  feed has reported a trip not run on some service day up to the period's
  last. Until then a feed that never sends the status would read 100%
  having measured nothing.

Every figure is computed from an ``Outcome``: the departures, each day's
service, and each day's timetable version. ``outcome`` derives the one the
truth implies. A test may derive another, such as the outcome a known
pipeline divergence produces, and hand it to the same figure functions. Such
an outcome may hold a departure no timetable could place, with no scheduled
time, no stop and the empty string for its day type. It counts in the figures
per route, per service date and per (route, service date, day type), and in
no figure pooled over days by day type or keyed by hour, scheduled time or
stop.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Callable, Hashable, Iterable
from dataclasses import dataclass
from datetime import date, timedelta
from fractions import Fraction

from tests.fixtures.scenario import Scenario

ON_TIME_MAX_LATE_SEC = 60
OVER_FIVE_MIN_SEC = 300
EARLY_SEC = 60


@dataclass(frozen=True)
class Departure:
    route_id: str
    trip_id: str
    service_date: date
    day_type: str
    stop_sequence: int
    stop_id: str | None
    scheduled_sec: int | None
    delay_sec: int

    @property
    def hour(self) -> int | None:
        return None if self.scheduled_sec is None else self.scheduled_sec // 3600

    @property
    def weekday(self) -> int:
        return self.service_date.isoweekday()


def before_midnight(d: Departure) -> bool:
    """Scheduled before 24:00:00 on its service day's clock."""
    return d.scheduled_sec is not None and d.scheduled_sec < 24 * 3600


def after_midnight(d: Departure) -> bool:
    """Scheduled at 24:00:00 or later on its service day's clock."""
    return d.scheduled_sec is not None and d.scheduled_sec >= 24 * 3600


Where = Callable[[Departure], bool] | None


@dataclass(frozen=True)
class Figures:
    departures: int
    delay_sum_sec: int
    on_time: int
    over_five_min: int
    worst_delay_sec: int
    trip_runs: int
    early: int
    on_time_industry: int

    @property
    def mean_delay_sec(self) -> Fraction:
        return Fraction(self.delay_sum_sec, self.departures)

    @property
    def on_time_share(self) -> Fraction:
        return Fraction(self.on_time, self.departures)

    @property
    def over_five_min_share(self) -> Fraction:
        return Fraction(self.over_five_min, self.departures)

    @property
    def early_share(self) -> Fraction:
        return Fraction(self.early, self.departures)

    @property
    def on_time_industry_share(self) -> Fraction:
        return Fraction(self.on_time_industry, self.departures)


@dataclass(frozen=True)
class Service:
    planned: int
    reported_not_run: int
    observed: int

    @property
    def executed(self) -> int:
        return self.planned - self.reported_not_run


@dataclass(frozen=True)
class Outcome:
    """What every figure is computed from: each departure, each service
    day's trips, each service day's timetable version, and each timetable
    version's trip count."""

    departures: tuple[Departure, ...]
    service: dict[date, Service]
    day_versions: dict[date, str]
    version_trips: dict[str, int]


def departures(scenario: Scenario) -> list[Departure]:
    out = []
    for run in scenario.runs:
        trip = scenario.trip(run.trip_id, run.service_date)
        stops = scenario.route(trip.route_id).stops
        for seq, (stop, scheduled, actual) in enumerate(
            zip(stops, trip.scheduled_sec, run.actual_sec, strict=True), start=1
        ):
            if actual is None:
                continue
            out.append(
                Departure(
                    trip.route_id,
                    trip.trip_id,
                    run.service_date,
                    trip.day_type,
                    seq,
                    stop.stop_id,
                    scheduled,
                    actual - scheduled,
                )
            )
    return out


def service(scenario: Scenario) -> dict[date, Service]:
    """Per service date, the planned, reported-not-run and observed trips."""
    return {
        day.date: Service(
            planned=len(scenario.scheduled(day.date)),
            reported_not_run=sum(1 for c in scenario.cancellations if c.service_date == day.date),
            observed=sum(1 for r in scenario.runs if r.service_date == day.date),
        )
        for day in scenario.days
    }


def outcome(scenario: Scenario) -> Outcome:
    """The outcome the scenario's truth implies. Each service date runs the
    timetable version in force that day, and each version counts one trip
    per trip it defines, whatever days it runs."""
    return Outcome(
        departures=tuple(departures(scenario)),
        service=service(scenario),
        day_versions={d.date: scenario.version_on(d.date).name for d in scenario.days},
        version_trips={v.name: len(v.trips) for v in scenario.versions},
    )


def figures(group: Iterable[Departure]) -> Figures:
    group = list(group)
    if not group:
        raise ValueError("no departures to summarise")
    delays = [d.delay_sec for d in group]
    return Figures(
        departures=len(group),
        delay_sum_sec=sum(delays),
        on_time=sum(1 for d in delays if d <= ON_TIME_MAX_LATE_SEC),
        over_five_min=sum(1 for d in delays if d > OVER_FIVE_MIN_SEC),
        worst_delay_sec=max(delays),
        trip_runs=len({(d.trip_id, d.service_date) for d in group}),
        early=sum(1 for d in delays if d < -EARLY_SEC),
        on_time_industry=sum(1 for d in delays if -EARLY_SEC <= d <= OVER_FIVE_MIN_SEC),
    )


def _grouped(found: Outcome, key: Callable[[Departure], Hashable], where: Where = None) -> dict:
    groups: dict = defaultdict(list)
    for d in found.departures:
        if where is None or where(d):
            groups[key(d)].append(d)
    return {k: figures(v) for k, v in groups.items()}


def _scheduled(where: Where) -> Callable[[Departure], bool]:
    return lambda d: d.scheduled_sec is not None and (where is None or where(d))


def by_route_day(found: Outcome) -> dict[tuple[str, date, str], Figures]:
    """Per (route, service date, day type)."""
    return _grouped(found, lambda d: (d.route_id, d.service_date, d.day_type))


def by_route_day_type(found: Outcome) -> dict[tuple[str, str], Figures]:
    """Per (route, day type), pooled over every service day."""
    return _grouped(found, lambda d: (d.route_id, d.day_type), _scheduled(None))


def by_route(found: Outcome) -> dict[str, Figures]:
    """Per route, pooled over every service day and day type."""
    return _grouped(found, lambda d: d.route_id)


def by_day(found: Outcome) -> dict[date, Figures]:
    """Per service date, every route together."""
    return _grouped(found, lambda d: d.service_date)


def by_day_hour(found: Outcome, where: Where = None) -> dict[tuple[date, int], Figures]:
    """Per (service date, hour), every route together."""
    return _grouped(found, lambda d: (d.service_date, d.hour), _scheduled(where))


def by_route_day_type_time(found: Outcome, where: Where = None) -> dict[tuple[str, str, int], Figures]:
    """Per (route, day type, scheduled departure in seconds), pooled over every service day."""
    return _grouped(found, lambda d: (d.route_id, d.day_type, d.scheduled_sec), _scheduled(where))


def by_route_day_type_weekday_hour(found: Outcome, where: Where = None) -> dict[tuple[str, str, int, int], Figures]:
    """Per (route, day type, weekday, hour), pooled over every service day."""
    return _grouped(found, lambda d: (d.route_id, d.day_type, d.weekday, d.hour), _scheduled(where))


def by_stop_day(found: Outcome) -> dict[tuple[str, date, str], Figures]:
    """Per (stop, service date, day type), every route through the stop together."""
    return _grouped(found, lambda d: (d.stop_id, d.service_date, d.day_type), lambda d: d.stop_id is not None)


def delivered_rate_shown(found: Outcome, last: date) -> bool:
    """Whether the feed reported a trip not run on some service day up to *last*."""
    return any(s.reported_not_run for day, s in found.service.items() if day <= last)


def revision_boundaries(found: Outcome) -> list[date]:
    """The service days whose timetable version differs from the previous
    calendar day's, both days having one."""
    return sorted(
        day for day, name in found.day_versions.items() if found.day_versions.get(day - timedelta(days=1), name) != name
    )
