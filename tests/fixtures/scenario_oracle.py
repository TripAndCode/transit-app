"""Expected figures computed from a scenario's declared truth and the polls
its feed model sends.

Nothing here imports pipeline or api code or their constants. Each definition
below is this oracle's own statement of what a figure means, so where the
pipeline disagrees, either the pipeline is wrong or it differs on purpose; the
scenario test says which.

Semantics assumed:

- A departure is one trip run leaving one stop: a (trip, service date, stop)
  triple. Every stop a run served is a departure, its last included, unless
  the feed never reported it plausibly (below).
- A stop a run skipped is no departure, and a trip that did not run, whether
  cancelled or never reported, has none: none of them has a delay, so none
  counts in any delay figure. A prediction the feed later withdraws, for a
  stop it then reports SKIPPED or a trip it then reports CANCELED, is not a
  departure either.
- The feed gives the delay, which the app collects rather than computes
  (``DELAY_ANALYSIS.md``, "The raw data"). A reading is one stop update of
  one poll that carries a departure delay. A reading of a delay over
  ``MAX_PLAUSIBLE_DELAY_SEC``, early or late, is implausible and dropped
  before deduplication, so a departure's delay is that of the last plausible
  reading of it, by capture time, and a served stop with no plausible
  reading is no departure (``docs/methodology.en.md``, "Excluding
  implausible values" and "Deduplication and withdrawal"). Under the
  ``perfect`` feed model each reading a misreport does not replace carries
  the true delay, the actual departure minus the scheduled departure in
  whole seconds, early negative; a frozen feed repeats such readings. Its
  schedule is the timetable version in force on its service date.
- A departure belongs to its trip's service date, whichever calendar day it
  falls on: a 24:10:00 departure is part of the service day that began the
  previous morning.
- A departure counts once, however many polls reported it, a frozen feed's
  repeats included. Where every reading of a departure carries its true
  delay, the sample counts are the only figures that exercise deduplication
  across polls; a misreport is what makes a departure's last plausible
  reading differ from its others.
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
- Observed: the planned trips the feed reported a plausible departure for
  (``DELAY_ANALYSIS.md``, "Feed-quality scorecard", trips reported): every
  run with a departure. A run whose every reading is implausible ran, but is
  not observed.
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

The feed's health, per ``DELAY_ANALYSIS.md`` ("Feed-quality scorecard" and
the stamp's clamp share under "Method versions") and the methodology's
"Feed quality":

- Readings are counted as the feed sent them, every poll's, not once per
  departure: a prediction repeated over ten polls is ten readings and one
  departure. Each reading, and each row the poll stores, belongs to the
  service day its poll was captured in, which turns over at
  ``SERVICE_DAY_CUTOVER_HOUR`` JST (methodology, "Service day and time").
- The clamp share over a set of days is their implausible readings over
  their readings, in percent; with no reading it has no figure.
- The share is flagged high when, printed to two decimals and that figure
  to a tenth, each ties away from zero, it is over ``MAX_CLAMP_PCT``
  (``docs/features/network-tab.md``, "Comparability gate": "5.04% prints
  5.0% and passes, 5.05% prints 5.1% and fails").
- A poll's message age is the instant it was captured minus its FeedHeader
  timestamp, so a header frozen over many polls reads a longer age on each.
  A poll is seen only through the rows it stored, and every poll a scenario
  sends stores at least one.
- The scorecard keeps one row per calendar month of service date. Its
  window runs from the month's first day through its last, but never past
  the newest service day with a departure, which in a scenario's one
  agency is the newest day any listed agency's aggregates reach. Its clamp
  share counts the window's readings, and its message age the window's
  polls: their count, and the ``MESSAGE_AGE_QUANTILES`` of their ages, each
  interpolated linearly between the two closest ranks.

Every figure is computed from an ``Outcome``: the departures, each day's
service, each day's timetable version, each route's scheduled headways
on each day, each day's readings, and each poll's age. ``outcome`` derives
the one the truth implies. A test may
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
from collections.abc import Callable, Hashable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from fractions import Fraction
from itertools import pairwise

from tests.fixtures.scenario import JST, SCHEDULED, Poll, Scenario, polls

# "A report of a delay over 120 minutes (7,200 seconds), early or late, is dropped."
MAX_PLAUSIBLE_DELAY_SEC = 120 * 60
# "Implausible readings over 5% as printed to a tenth" are flagged.
MAX_CLAMP_PCT = 5
# "Service day: turns over at 04:00 JST."
SERVICE_DAY_CUTOVER_HOUR = 4
# "The median and 95th percentile" of the message age.
MESSAGE_AGE_QUANTILES = (Fraction(1, 2), Fraction(19, 20))
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
class Readings:
    """Readings as the polls of one service day or more sent them: how many
    carried a delay, how many of those were implausible, and the rows the
    polls stored."""

    readings: int
    implausible: int
    rows: int

    @property
    def clamp_share(self) -> Fraction | None:
        """The implausible readings of the readings, in percent; None without a reading."""
        return Fraction(100 * self.implausible, self.readings) if self.readings else None


@dataclass(frozen=True)
class Outcome:
    """What every figure is computed from: each departure, each service
    day's trips, each service day's timetable version, each timetable
    version's trip count, per (route, service date) the plan its long
    gaps are judged by (``plans``) and the plan its high-frequency test and
    scheduled mean wait read (``wait_plans``), each service day's readings,
    and each poll's message age under the service day it was captured in.
    The truth's two plans are one: the timetable in force that day."""

    departures: tuple[Departure, ...]
    service: dict[date, Service]
    day_versions: dict[date, str]
    version_trips: dict[str, int]
    plans: dict[tuple[str, date], Plan]
    wait_plans: dict[tuple[str, date], Plan]
    readings: dict[date, Readings]
    ages: tuple[tuple[date, int], ...]


def plausible(delay_sec: int) -> bool:
    return -MAX_PLAUSIBLE_DELAY_SEC <= delay_sec <= MAX_PLAUSIBLE_DELAY_SEC


def service_day_of(instant: datetime) -> date:
    """The service day *instant* falls in."""
    return (instant.astimezone(JST) - timedelta(hours=SERVICE_DAY_CUTOVER_HOUR)).date()


def _last_plausible(sent: Sequence[Poll]) -> dict[tuple[str, date, int], int]:
    """Per (trip, service date, stop sequence), the delay of its last
    plausible reading by capture time."""
    last: dict[tuple[str, date, int], tuple[datetime, int]] = {}
    for poll in sent:
        for entry in poll.trips:
            for stop in entry.stops:
                delay = stop.departure_delay
                if stop.status != SCHEDULED or delay is None or not plausible(delay):
                    continue
                key = (entry.trip_id, entry.service_date, stop.stop_sequence)
                if key not in last or last[key][0] <= poll.captured_at:
                    last[key] = (poll.captured_at, delay)
    return {key: delay for key, (_at, delay) in last.items()}


def departures(scenario: Scenario, sent: Sequence[Poll]) -> list[Departure]:
    """Every served stop of every run with a plausible reading in *sent*,
    at that last reading's delay.

    Raises when a run no misreport names was reported at a delay other than
    its true one, or a true delay inside the clamp went without a plausible
    reading: the feed model would then be at fault, not the pipeline."""
    last = _last_plausible(sent)
    misreported = {(m.trip_id, m.service_date) for m in scenario.feed.misreports}
    out = []
    for run in scenario.runs:
        trip = scenario.trip(run.trip_id, run.service_date)
        stops = scenario.route(trip.route_id).stops
        for seq, (stop, scheduled, actual) in enumerate(
            zip(stops, trip.scheduled_sec, run.actual_sec, strict=True), start=1
        ):
            if actual is None:
                continue
            reported = last.get((run.trip_id, run.service_date, seq))
            truthful = reported == actual - scheduled if plausible(actual - scheduled) else reported is None
            if not truthful and (run.trip_id, run.service_date) not in misreported:
                raise ValueError(f"{run.trip_id} {run.service_date} stop {seq}: reported {reported}, not its truth")
            if reported is None:
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
                    reported,
                )
            )
    return out


def service(scenario: Scenario, found: Iterable[Departure]) -> dict[date, Service]:
    """Per service date, the planned, reported-not-run and observed trips,
    observed among the departures *found*."""
    observed = {(d.trip_id, d.service_date) for d in found}
    return {
        day.date: Service(
            planned=len(scenario.scheduled(day.date)),
            reported_not_run=sum(1 for c in scenario.cancellations if c.service_date == day.date),
            observed=sum(1 for r in scenario.runs if r.service_date == day.date and (r.trip_id, day.date) in observed),
        )
        for day in scenario.days
    }


def readings(sent: Iterable[Poll]) -> dict[date, Readings]:
    """Per service day of capture, the readings and rows *sent*."""
    counts: dict[date, list[int]] = defaultdict(lambda: [0, 0, 0])
    for poll in sent:
        day = counts[service_day_of(poll.captured_at)]
        delays = [s.departure_delay for t in poll.trips for s in t.stops if s.departure_delay is not None]
        day[0] += len(delays)
        day[1] += sum(1 for d in delays if not plausible(d))
        day[2] += poll.stored_rows
    return {day: Readings(*c) for day, c in counts.items()}


def poll_ages(sent: Iterable[Poll]) -> tuple[tuple[date, int], ...]:
    """Each poll's service day of capture and message age, in seconds."""
    return tuple(
        (service_day_of(p.captured_at), int((p.captured_at - p.header).total_seconds())) for p in sent if p.stored_rows
    )


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
    """The outcome the scenario's truth and its feed's polls imply. Each
    service date runs the timetable version in force that day, and each
    version counts one trip per trip it defines, whatever days it runs."""
    sent = polls(scenario)
    found = tuple(departures(scenario, sent))
    day_plans = plans(scenario)
    return Outcome(
        departures=found,
        service=service(scenario, found),
        day_versions={d.date: scenario.version_on(d.date).name for d in scenario.days},
        version_trips={v.name: len(v.trips) for v in scenario.versions},
        plans=day_plans,
        wait_plans=day_plans,
        readings=readings(sent),
        ages=poll_ages(sent),
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


def readings_over(found: Outcome, first: date, last: date) -> Readings:
    """The readings of the service days *first* to *last*, summed."""
    days = [r for day, r in found.readings.items() if first <= day <= last]
    return Readings(
        readings=sum(r.readings for r in days),
        implausible=sum(r.implausible for r in days),
        rows=sum(r.rows for r in days),
    )


def _printed(value: Fraction, places: int) -> Fraction:
    """*value* to *places* decimals, ties away from zero."""
    scale = 10**places
    magnitude = Fraction(math.floor(abs(value) * scale + Fraction(1, 2)), scale)
    return magnitude if value >= 0 else -magnitude


def clamp_high(share: Fraction | None) -> bool:
    """Whether *share*, printed to two decimals and then to a tenth, is over ``MAX_CLAMP_PCT``."""
    return share is not None and _printed(_printed(share, 2), 1) > MAX_CLAMP_PCT


def quantile(values: Iterable[int], q: Fraction) -> Fraction:
    """The *q* quantile of *values*, interpolated linearly between the two
    closest ranks: rank (n - 1) q counting from 0."""
    ordered = sorted(values)
    if not ordered:
        raise ValueError("no values to take a quantile of")
    rank = (len(ordered) - 1) * q
    below = math.floor(rank)
    if below == rank:
        return Fraction(ordered[below])
    return ordered[below] + (ordered[below + 1] - ordered[below]) * (rank - below)


@dataclass(frozen=True)
class Scorecard:
    """One month of the feed-quality scorecard: its window's last day, its
    readings, and its polls' count and message age quantiles (None without a
    poll)."""

    last: date
    readings: Readings
    polls: int
    ages: tuple[Fraction, ...] | None


def _month(day: date) -> date:
    return day.replace(day=1)


def _month_end(month: date) -> date:
    return (month + timedelta(days=32)).replace(day=1) - timedelta(days=1)


def scorecard(found: Outcome) -> dict[date, Scorecard]:
    """Per calendar month, from the month of the first service day the feed
    stored a row on through the month of the newest service day with a
    departure, that month's scorecard."""
    if not found.departures:
        raise ValueError("the scorecard needs a departure: its window ends at the newest service day with one")
    newest = max(d.service_date for d in found.departures)
    first = min(day for day, r in found.readings.items() if r.rows)
    out = {}
    month = _month(first)
    while month <= newest:
        last = min(_month_end(month), newest)
        ages = [age for day, age in found.ages if month <= day <= last]
        out[month] = Scorecard(
            last=last,
            readings=readings_over(found, month, last),
            polls=len(ages),
            ages=tuple(quantile(ages, q) for q in MESSAGE_AGE_QUANTILES) if ages else None,
        )
        month = _month_end(month) + timedelta(days=1)
    return out
