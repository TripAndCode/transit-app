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
- A departure's time band is the one of ``TIME_BANDS`` its scheduled
  departure falls in on the wall clock, each band including its start and
  excluding its end: 24:10:00 is 00:10, in the late night. A departure with
  no scheduled time is in no band.

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

Headways, per the methodology's headway metrics (``docs/methodology.en.md``,
"Headway metrics") and the method's baseline, under which "each day's long
gaps are measured against that timetable's own headway" (``DELAY_ANALYSIS.md``,
method version ``2026.11.0``):

- A stop's actual headways on a service day are the gaps between consecutive
  departures from it by the route's trip runs of that service day, in the
  order they left: a run that overtakes another leaves the stop first. Only
  departures count, so a trip that did not run or a stop a run skipped
  leaves a wider gap around it. A gap never spans two stops or two service
  days. Two runs leaving one stop in the same second are refused: a gap of
  zero cannot be told apart from one departure reported twice, so no
  scenario may rest on how it counts.
- A route's actual headways on a service day are every stop's, pooled.
- Its scheduled headways that day are the same gaps between the scheduled
  departures of every trip the timetable in force that day schedules for
  the day's type, run or not. The day's scheduled headway is their median.
- The median is the middle value, or the mean of the middle two of an even
  count.
- A route is high-frequency on a service day when that day's scheduled
  headway is at most ``HIGH_FREQUENCY_MAX_HEADWAY_SEC``.
- A long gap is an actual gap longer than ``LONG_GAP_FACTOR`` times the day's
  scheduled headway. A day the timetable gives no headway has no long gap.
- The mean wait over a set of gaps is the average wait of a passenger who
  arrives at a uniformly random instant of the time they span: a gap of h
  seconds holds h of the span and an average wait of h/2 inside it, so the
  mean is the sum of h squared over twice the sum of h. Gaps pooled over
  stops or days are pooled by those sums.
- Over a period, a route's headway quality pools the days of the period on
  which it is high-frequency and has actual headways. Excess waiting time is
  the pooled actual mean wait minus the same days' pooled scheduled mean
  wait. The coefficient of variation is the pooled gaps' population
  standard deviation, the spread of the gaps observed rather than an
  estimate of a wider population's, over their mean, defined only for two
  gaps or more. The long-gap share is the pooled long gaps over the pooled
  gaps. A route high-frequency on no such day is not listed.

Every figure is computed from an ``Outcome``: the departures, each day's
service, each day's timetable version, and each route's scheduled headways
on each day. ``outcome`` derives the one the truth implies. A test may
derive another, such as the outcome a known pipeline divergence produces,
and hand it to the same figure functions. Such an outcome may hold a
departure no timetable could place, with no scheduled time, no stop and the
empty string for its day type. It counts in the figures
per route, per service date and per (route, service date, day type), and in
no figure pooled over days by day type or keyed by hour, scheduled time or
stop, nor in any headway.
"""

from __future__ import annotations

import math
from collections import defaultdict
from collections.abc import Callable, Hashable, Iterable, Mapping
from dataclasses import dataclass
from datetime import date, timedelta
from fractions import Fraction
from itertools import pairwise

from tests.fixtures.scenario import Scenario

# "A route whose median scheduled headway is 12 minutes or less is high-frequency."
HIGH_FREQUENCY_MAX_HEADWAY_SEC = 12 * 60
# "The share of gaps longer than 1.75 times the scheduled headway."
LONG_GAP_FACTOR = Fraction(7, 4)
ON_TIME_MAX_LATE_SEC = 60
OVER_FIVE_MIN_SEC = 300
EARLY_SEC = 60
# Each band's first and end wall-clock hour.
TIME_BANDS = {
    "morning": (5, 9),
    "forenoon": (9, 12),
    "noon": (12, 14),
    "afternoon": (14, 17),
    "evening": (17, 20),
    "night": (20, 24),
    "late_night": (0, 5),
}


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

    @property
    def time_band(self) -> str | None:
        if self.scheduled_sec is None:
            return None
        wall = self.scheduled_sec % (24 * 3600)
        return next(band for band, (first, end) in TIME_BANDS.items() if first * 3600 <= wall < end * 3600)


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
class Plan:
    """A route's scheduled headways on one service day: every scheduled
    gap, pooled over its stops in ascending order, and the day's scheduled
    headway, None when there is no gap."""

    gaps: tuple[int, ...]
    headway_sec: Fraction | None

    @property
    def high_frequency(self) -> bool:
        return self.headway_sec is not None and self.headway_sec <= HIGH_FREQUENCY_MAX_HEADWAY_SEC


@dataclass(frozen=True)
class Outcome:
    """What every figure is computed from: each departure, each service
    day's trips, each service day's timetable version, each timetable
    version's trip count, and per (route, service date) the plan its long
    gaps are judged by (``plans``) and the plan its high-frequency test and
    scheduled mean wait read (``wait_plans``). The truth's two are one plan:
    the timetable in force that day."""

    departures: tuple[Departure, ...]
    service: dict[date, Service]
    day_versions: dict[date, str]
    version_trips: dict[str, int]
    plans: dict[tuple[str, date], Plan]
    wait_plans: dict[tuple[str, date], Plan]


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


def median(values: Iterable[int]) -> Fraction | None:
    """The middle value, the mean of the middle two of an even count, or None for none."""
    ordered = sorted(values)
    if not ordered:
        return None
    middle = len(ordered) // 2
    if len(ordered) % 2:
        return Fraction(ordered[middle])
    return Fraction(ordered[middle - 1] + ordered[middle], 2)


def _gaps(times_by_stop: Mapping[str, list[int]], label: str) -> tuple[int, ...]:
    """Each stop's gaps between consecutive *times*, pooled in ascending order."""
    gaps = []
    for stop_id, times in times_by_stop.items():
        for earlier, later in pairwise(sorted(times)):
            if later == earlier:
                raise ValueError(f"{label}: two departures from {stop_id} in the same second")
            gaps.append(later - earlier)
    return tuple(sorted(gaps))


def plans(scenario: Scenario) -> dict[tuple[str, date], Plan]:
    """Per (route, service date), the scheduled headways of the timetable in
    force that day, for every route it schedules a trip on."""
    out = {}
    for day in scenario.days:
        times: dict[str, dict[str, list[int]]] = defaultdict(lambda: defaultdict(list))
        for trip in scenario.scheduled(day.date):
            for stop, sec in zip(scenario.route(trip.route_id).stops, trip.scheduled_sec, strict=True):
                times[trip.route_id][stop.stop_id].append(sec)
        for route_id, by_stop in times.items():
            gaps = _gaps(by_stop, f"{route_id} {day.date} timetable")
            out[(route_id, day.date)] = Plan(gaps, median(gaps))
    return out


def outcome(scenario: Scenario) -> Outcome:
    """The outcome the scenario's truth implies. Each service date runs the
    timetable version in force that day, and each version counts one trip
    per trip it defines, whatever days it runs."""
    day_plans = plans(scenario)
    return Outcome(
        departures=tuple(departures(scenario)),
        service=service(scenario),
        day_versions={d.date: scenario.version_on(d.date).name for d in scenario.days},
        version_trips={v.name: len(v.trips) for v in scenario.versions},
        plans=day_plans,
        wait_plans=day_plans,
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


def by_route(found: Outcome, where: Where = None) -> dict[str, Figures]:
    """Per route, pooled over every service day and day type."""
    return _grouped(found, lambda d: d.route_id, where)


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


@dataclass(frozen=True)
class HeadwayDay:
    """A route's actual headways on one service day, pooled over its stops
    in ascending order, and how many of them are long."""

    gaps: tuple[int, ...]
    long_gaps: int

    @property
    def samples(self) -> int:
        return len(self.gaps)

    @property
    def median_sec(self) -> Fraction:
        found = median(self.gaps)
        assert found is not None
        return found

    @property
    def sum_sec(self) -> int:
        return sum(self.gaps)

    @property
    def sumsq_sec2(self) -> int:
        return sum(g * g for g in self.gaps)


def headway_days(found: Outcome) -> dict[tuple[str, date], HeadwayDay]:
    """Per (route, service date) with at least one actual gap, its headways,
    long gaps judged by the day's plan."""
    times: dict[tuple[str, date], dict[str, list[int]]] = defaultdict(lambda: defaultdict(list))
    for d in found.departures:
        if d.stop_id is not None and d.scheduled_sec is not None:
            times[(d.route_id, d.service_date)][d.stop_id].append(d.scheduled_sec + d.delay_sec)
    out = {}
    for key, by_stop in times.items():
        gaps = _gaps(by_stop, f"{key[0]} {key[1]}")
        if not gaps:
            continue
        plan = found.plans.get(key)
        headway = None if plan is None else plan.headway_sec
        long_gaps = 0 if headway is None else sum(1 for g in gaps if g > LONG_GAP_FACTOR * headway)
        out[key] = HeadwayDay(gaps, long_gaps)
    return out


@dataclass(frozen=True)
class HeadwayQuality:
    """A route's headways pooled over a period's high-frequency days: the
    actual gaps' count, sum, sum of squares and long gaps, and the scheduled
    gaps' sum and sum of squares over the same days."""

    samples: int
    sum_sec: int
    sumsq_sec2: int
    long_gaps: int
    scheduled_sum_sec: int
    scheduled_sumsq_sec2: int

    @property
    def actual_wait_sec(self) -> Fraction:
        return Fraction(self.sumsq_sec2, 2 * self.sum_sec)

    @property
    def scheduled_wait_sec(self) -> Fraction:
        return Fraction(self.scheduled_sumsq_sec2, 2 * self.scheduled_sum_sec)

    @property
    def excess_wait_sec(self) -> Fraction:
        return self.actual_wait_sec - self.scheduled_wait_sec

    @property
    def cov_squared(self) -> Fraction | None:
        """The squared coefficient of variation: the mean square over the
        squared mean, less one."""
        if self.samples < 2:
            return None
        return Fraction(self.samples * self.sumsq_sec2, self.sum_sec**2) - 1

    @property
    def cov(self) -> float | None:
        squared = self.cov_squared
        return None if squared is None else math.sqrt(squared)

    @property
    def long_gap_share(self) -> Fraction:
        return Fraction(self.long_gaps, self.samples)


def headway_quality(found: Outcome, first: date, last: date) -> dict[str, HeadwayQuality]:
    """Per route, its headway quality over the service days *first* to *last*."""
    pooled: dict[str, list[tuple[HeadwayDay, Plan]]] = defaultdict(list)
    for (route_id, day), headways in headway_days(found).items():
        plan = found.wait_plans.get((route_id, day))
        if first <= day <= last and plan is not None and plan.high_frequency:
            pooled[route_id].append((headways, plan))
    return {
        route_id: HeadwayQuality(
            samples=sum(h.samples for h, _plan in days),
            sum_sec=sum(h.sum_sec for h, _plan in days),
            sumsq_sec2=sum(h.sumsq_sec2 for h, _plan in days),
            long_gaps=sum(h.long_gaps for h, _plan in days),
            scheduled_sum_sec=sum(sum(plan.gaps) for _h, plan in days),
            scheduled_sumsq_sec2=sum(g * g for _h, plan in days for g in plan.gaps),
        )
        for route_id, days in pooled.items()
    }


def no_high_frequency_route(found: Outcome, first: date, last: date) -> bool:
    """Whether no route is high-frequency on any service day from *first* to *last*."""
    return not any(plan.high_frequency for (_route, day), plan in found.wait_plans.items() if first <= day <= last)
