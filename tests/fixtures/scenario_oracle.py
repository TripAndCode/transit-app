"""Expected delay figures computed from a scenario's declared truth alone.

Nothing here imports pipeline or api code or their constants. Each definition
below is this oracle's own statement of what a figure means, so where the
pipeline disagrees, either the pipeline is wrong or it differs on purpose; the
scenario test says which.

Semantics assumed:

- A departure is one trip run leaving one stop: a (trip, service date, stop)
  triple. Every stop of a trip is a departure, its last included.
- Its delay is the actual departure minus the scheduled departure, in whole
  seconds. Early is negative.
- A departure belongs to its trip's service date, whichever calendar day it
  falls on: a 24:10:00 departure is part of the service day that began the
  previous morning.
- A departure counts once, however many polls reported it. Under the
  ``perfect`` feed model every poll already carries the true delay, so the
  sample counts are the only figures that exercise deduplication across polls.
- On time (the legacy rule): left early by any amount, or at most
  ``ON_TIME_MAX_LATE_SEC`` late.
- Over five minutes late: more than ``OVER_FIVE_MIN_SEC`` late.
- Mean delay: the plain mean over departures, early ones counting negative.
- A departure's hour is the whole hour of its scheduled departure on the
  service-day clock: 07:58:00 is hour 7, and 24:10:00 is hour 24 of the
  service day its trip belongs to. Its weekday is its service date's ISO
  weekday, Monday 1.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Callable, Hashable, Iterable
from dataclasses import dataclass
from datetime import date
from fractions import Fraction

from tests.fixtures.scenario import Scenario

ON_TIME_MAX_LATE_SEC = 60
OVER_FIVE_MIN_SEC = 300


@dataclass(frozen=True)
class Departure:
    route_id: str
    trip_id: str
    service_date: date
    day_type: str
    stop_sequence: int
    scheduled_sec: int
    delay_sec: int

    @property
    def hour(self) -> int:
        return self.scheduled_sec // 3600

    @property
    def weekday(self) -> int:
        return self.service_date.isoweekday()


def before_midnight(d: Departure) -> bool:
    """Scheduled before 24:00:00 on its service day's clock."""
    return d.scheduled_sec < 24 * 3600


Where = Callable[[Departure], bool] | None


@dataclass(frozen=True)
class Figures:
    departures: int
    delay_sum_sec: int
    on_time: int
    over_five_min: int
    worst_delay_sec: int
    trip_runs: int

    @property
    def mean_delay_sec(self) -> Fraction:
        return Fraction(self.delay_sum_sec, self.departures)

    @property
    def on_time_share(self) -> Fraction:
        return Fraction(self.on_time, self.departures)

    @property
    def over_five_min_share(self) -> Fraction:
        return Fraction(self.over_five_min, self.departures)


def departures(scenario: Scenario) -> list[Departure]:
    out = []
    for run in scenario.runs:
        trip = scenario.trip(run.trip_id)
        for seq, (scheduled, actual) in enumerate(zip(trip.scheduled_sec, run.actual_sec, strict=True), start=1):
            out.append(
                Departure(
                    trip.route_id, trip.trip_id, run.service_date, trip.day_type, seq, scheduled, actual - scheduled
                )
            )
    return out


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
    )


def _grouped(scenario: Scenario, key: Callable[[Departure], Hashable], where: Where = None) -> dict:
    groups: dict = defaultdict(list)
    for d in departures(scenario):
        if where is None or where(d):
            groups[key(d)].append(d)
    return {k: figures(v) for k, v in groups.items()}


def by_route_day(scenario: Scenario) -> dict[tuple[str, date, str], Figures]:
    """Per (route, service date, day type)."""
    return _grouped(scenario, lambda d: (d.route_id, d.service_date, d.day_type))


def by_route_day_type(scenario: Scenario, where: Where = None) -> dict[tuple[str, str], Figures]:
    """Per (route, day type), pooled over every service day."""
    return _grouped(scenario, lambda d: (d.route_id, d.day_type), where)


def by_route(scenario: Scenario) -> dict[str, Figures]:
    """Per route, pooled over every service day and day type."""
    return _grouped(scenario, lambda d: d.route_id)


def by_day(scenario: Scenario, where: Where = None) -> dict[date, Figures]:
    """Per service date, every route together."""
    return _grouped(scenario, lambda d: d.service_date, where)


def by_day_hour(scenario: Scenario, where: Where = None) -> dict[tuple[date, int], Figures]:
    """Per (service date, hour), every route together."""
    return _grouped(scenario, lambda d: (d.service_date, d.hour), where)


def by_route_day_type_time(scenario: Scenario, where: Where = None) -> dict[tuple[str, str, int], Figures]:
    """Per (route, day type, scheduled departure in seconds), pooled over every service day."""
    return _grouped(scenario, lambda d: (d.route_id, d.day_type, d.scheduled_sec), where)


def by_route_day_type_weekday_hour(scenario: Scenario, where: Where = None) -> dict[tuple[str, str, int, int], Figures]:
    """Per (route, day type, weekday, hour), pooled over every service day."""
    return _grouped(scenario, lambda d: (d.route_id, d.day_type, d.weekday, d.hour), where)
