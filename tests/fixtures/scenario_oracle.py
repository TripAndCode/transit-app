"""Expected figures computed from a scenario's declared truth and the polls
its feed model sends.

Nothing here imports pipeline or api code or their constants. Each definition
below is this oracle's own statement of what a figure means, so where the
pipeline disagrees, either the pipeline is wrong or it differs on purpose; the
scenario test says which.

Semantics assumed:

- A departure is one trip run leaving one stop: a (trip, service date, stop)
  triple. Every stop a run served is a departure, its last included, unless
  the feed never reported it plausibly (below), or the feed reports
  arrivals: such a feed reports a run's last stop only as an arrival, so the
  last stop is no departure.
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
  implausible values" and "Deduplication and withdrawal"). A reading is
  of the departure of its trip and stop on the service day it is filed
  under, which its capture decides, not the run its poll names: the
  service day its capture falls in, or the day either side of it when the
  stop's scheduled departure that day is within ``FILING_REACH_SEC`` of the
  capture ("Service day and time"). One captured more than
  ``LATEST_READING_SEC`` after that departure is no reading of it: a feed
  lists a trip again long after it ran ("Reports received after the
  departure"). It still counts as a reading the feed sent. Under the
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

Dwell and running time, per the pairing ``docs/features/analysis-tab.md``
describes for the ``dwell_run`` report, the user manual's glossary
(``frontend/public/user-manual/en.md``, "Dwell" and "Running") and the
methodology's "Dwell and running time" (``docs/methodology.en.md``):

- A stop visit is one trip run at one stop on its service date. The feed
  reports a visit's arrival and its departure in separate stop updates. Its
  arrival is that of its last plausible arrival reading by capture time, and
  its departure is the departure above: each side is the latest plausible
  reading of its own kind, under the same clamp. A visit with neither has no
  figure at all. No scenario withdraws an arrival: a feed that reports
  arrivals runs every trip it schedules and skips no stop (``FeedModel``).
- The actual arrival is the scheduled arrival plus the arrival delay, and
  the actual departure the scheduled departure plus the departure delay,
  each scheduled time from the timetable in force on the service date.
- A dwell sample is a visit's actual departure less its actual arrival,
  counted only when the feed reported that departure after it reported the
  arrival: the departure's last plausible reading came at or after the
  visit's first plausible arrival reading, polls ordered by capture time and
  then file name. A departure reported only before the arrival is a
  prediction, and gives no dwell. A run's last stop, reported only as an
  arrival, gives none either.
- A running sample is a visit's actual arrival less the actual departure of
  the run's visit before it, the nearest earlier stop with a visit, however
  that departure was reported. A run's first visit has none, nor does a
  visit after one with no departure, nor a visit with no arrival.
- No bound applies to a dwell or a running time itself; only each reading's
  clamp does.
- Per (route, service date, day type) with a visit, the samples, none when
  no visit pairs. Over a period, a route with a visit pools its days'
  samples; one with neither a dwell nor a running sample is not listed.

The paths a poll reaches history (``updates``) by, per the data path
``docs/deploy-railway.md`` sets out ("Production data path — the live path,
promoted daily" under "Load data", and the daily ingest job's "This job
writes closed JST days only, and never a day the live path already
promoted"), ``README.md``'s architecture notes and the methodology's "How
the data is collected" (``docs/methodology.en.md``):

- The collector keeps every poll it captures in its archive and pushes the
  app only some of them (``FeedModel``). Live, a day reaches history as the
  polls pushed, once promotion copies the closed day; by the archive, as
  every poll it captured.
- A day here is a JST calendar day of capture: promotion copies closed JST
  days, and archive ingest writes closed JST days only.
- "A day in ``updates`` has one source": whichever path stores a row of the
  day there first holds the day, and no poll of it the other path offers
  reaches history. The paths store in the order the operator takes them
  (``Handoff``): the archive ingested before promotion, promotion, then the
  archive ingested after it. So, as the guide warns, "promotion claims each
  day before its archive could be ingested" unless the archive comes first.
- Every figure above is computed from the polls that reach history. On a
  day the live path holds, a departure no pushed poll reported is no
  departure, and a departure's delay is that of the last pushed reading of
  it, whatever the archive read later.

The source of a day's observations, per the methodology's "Where the
observations came from" and the definition stamp's ``source_mix``
(``docs/features/analysis-tab.md``):

- Each row a poll stores came by the path its poll reached history by,
  and belongs to the service day the poll was captured in, so a service
  day that runs past midnight can hold rows of both paths.
- A service day's source is the path holding most of its rows: the archive
  only when its rows outnumber the live ones, since "a tie reads live". A
  day with no row has none.
- Over a period, its days by source, archive, live and none, which add up
  to the period's days.

The feed's predictions against the truth. Every figure is "computed from
the delay the operator's feed last predicted for each stop. It is not an
observation of the bus passing the stop" (the methodology's "Value basis
and validation status"), and of a stop's reports the one that counts is
"the last report received" (its "Deduplication and withdrawal"; "keep only
the latest reading per trip-stop-day", ``DELAY_ANALYSIS.md``'s dedup). So:

- Under a feed that predicts imperfectly (``FeedModel``), a departure's
  delay is the prediction of the last poll that read it plausibly, not its
  true delay. A poll captured at t lists a stop of a run while t less the
  feed's freshness lag is before the stop's departure. Under
  ``propagate_current_delay`` it predicts each stop it lists at the run's
  delay as it stood at t less the lag: that of the last stop it had left
  by then, a stop being left from the instant of its departure, or 0
  before it left its first. Under the perfect model it predicts each
  stop's true delay, lag or not. So the last poll to list a stop is the
  last on the poll interval's grid before its departure plus the lag, and
  under a lag it may be captured after the run left.
- A departure's prediction error is its delay less its true delay, late
  positive: a feed that reports a run later than it left overstates its
  delay. Its lead time is its true departure instant less the capture
  instant of the poll whose reading it took, negative for a reading
  captured after the run left. The lead runs to the true departure, not,
  as in ``pipeline.prediction_accuracy``, to the reading's own predicted
  one, since the truth is what a passage observed from vehicle positions
  stands for.
- Over a set of departures, the mean absolute error and the bias, the
  mean error. By lead time, each group holds the leads from a multiple of
  a width to the next multiple, that one excluded, so a negative lead
  falls in a group of its own.

Every figure is computed from an ``Outcome``: the departures, each day's
service, each day's timetable version, each route's scheduled headways
on each day, each day's readings and rows by path, each poll's age, each
stop visit, and each departure's prediction beside its truth.
``outcome`` derives the one the truth implies. A test may
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
from datetime import date, datetime, timedelta, timezone
from fractions import Fraction
from itertools import pairwise
from typing import Literal

from tests.fixtures.scenario import (
    JST,
    SCHEDULED,
    FeedModel,
    Poll,
    Scenario,
    ScheduledTrip,
    TripRun,
    capture_day,
    handoff,
    polls,
    service_day_instant,
)

# "A report of a delay over 120 minutes (7,200 seconds), early or late, is dropped."
MAX_PLAUSIBLE_DELAY_SEC = 120 * 60
# "A report received more than 130 minutes after the stop's scheduled departure is ignored."
LATEST_READING_SEC = 130 * 60
# "A report counts toward the service day before or after the one it was received on when the
# stop's scheduled departure that day is within four hours of it."
FILING_REACH_SEC = 4 * 3600
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


IngestPath = Literal["archive", "live"]


@dataclass(frozen=True)
class Sources:
    """A service day's rows by the path they reached history by."""

    archive_rows: int
    live_rows: int

    @property
    def source(self) -> IngestPath | None:
        """The path holding most of the rows, live on a tie; None without a row."""
        if not self.archive_rows + self.live_rows:
            return None
        return "archive" if self.archive_rows > self.live_rows else "live"


@dataclass(frozen=True)
class Outcome:
    """What every figure is computed from: each departure, each service
    day's trips, each service day's timetable version, each timetable
    version's trip count, per (route, service date) the plan its long
    gaps are judged by (``plans``) and the plan its high-frequency test and
    scheduled mean wait read (``wait_plans``), each service day's readings,
    each poll's message age under the service day it was captured in, each
    stop visit, each service day's rows by the path they came by, and each
    departure's prediction beside its truth. The truth's two plans are one:
    the timetable in force that day."""

    departures: tuple[Departure, ...]
    service: dict[date, Service]
    day_versions: dict[date, str]
    version_trips: dict[str, int]
    plans: dict[tuple[str, date], Plan]
    wait_plans: dict[tuple[str, date], Plan]
    readings: dict[date, Readings]
    ages: tuple[tuple[date, int], ...]
    visits: tuple[Visit, ...]
    sources: dict[date, Sources]
    predictions: tuple[Prediction, ...]


def plausible(delay_sec: int) -> bool:
    return -MAX_PLAUSIBLE_DELAY_SEC <= delay_sec <= MAX_PLAUSIBLE_DELAY_SEC


def service_day_of(instant: datetime) -> date:
    """The service day *instant* falls in."""
    return (instant.astimezone(JST) - timedelta(hours=SERVICE_DAY_CUTOVER_HOUR)).date()


@dataclass(frozen=True)
class Received:
    """A poll that reached history, and the path it came by."""

    poll: Poll
    path: IngestPath


def received(scenario: Scenario, captured: Sequence[Poll]) -> list[Received]:
    """The polls of *captured* that reach history, each with its path: each
    step of the scenario's ``Handoff`` in turn stores its polls of every day
    no other path holds yet, and holds each day it stores a row of."""
    steps = handoff(scenario, captured)
    held: dict[date, IngestPath] = {}
    out = []
    step: tuple[Poll, ...]
    path: IngestPath
    for path, step in (("archive", steps.archived_first), ("live", steps.pushed), ("archive", steps.archived_after)):
        stored = [p for p in step if held.get(capture_day(p), path) == path]
        for poll in stored:
            if poll.stored_rows:
                held.setdefault(capture_day(poll), path)
        out += [Received(poll, path) for poll in stored]
    return out


def sources(arrived: Iterable[Received]) -> dict[date, Sources]:
    """Per service day of capture, the rows *arrived* stored by each path."""
    rows: dict[date, dict[IngestPath, int]] = defaultdict(lambda: {"archive": 0, "live": 0})
    for r in arrived:
        rows[service_day_of(r.poll.captured_at)][r.path] += r.poll.stored_rows
    return {day: Sources(archive_rows=n["archive"], live_rows=n["live"]) for day, n in rows.items()}


def _order(poll: Poll) -> tuple[datetime, str]:
    """Where *poll* stands among the polls: by capture time, then file name."""
    return (poll.captured_at, poll.source_file)


def filed_day(captured_at: datetime, scheduled_sec: int) -> date:
    """The service day a reading captured at *captured_at* of a stop the
    timetable schedules at *scheduled_sec* is filed under: the day either
    side of its capture's whose departure of the stop is within
    ``FILING_REACH_SEC`` of the capture, else its capture's own."""
    own = service_day_of(captured_at)
    reach = timedelta(seconds=FILING_REACH_SEC)
    for day in (own + timedelta(days=1), own - timedelta(days=1)):
        if abs(service_day_instant(day, scheduled_sec) - captured_at) <= reach:
            return day
    return own


def _plausible_readings(
    scenario: Scenario, sent: Sequence[Poll], kind: Literal["arrival", "departure"]
) -> dict[tuple[str, date, int], list[tuple[tuple[datetime, str], int]]]:
    """Per (trip, service date, stop sequence), each plausible reading of
    *kind* in *sent* filed under that date (``filed_day``) and captured by
    ``LATEST_READING_SEC`` after the stop's scheduled departure on it,
    earliest first: where its poll stands (``_order``) and its delay. The
    schedule is the one of the run the poll names, as ingest joins it."""
    out: dict[tuple[str, date, int], list[tuple[tuple[datetime, str], int]]] = defaultdict(list)
    latest = timedelta(seconds=LATEST_READING_SEC)
    for poll in sorted(sent, key=_order):
        for entry in poll.trips:
            scheduled = scenario.trip(entry.trip_id, entry.service_date).scheduled_sec
            for stop in entry.stops:
                delay = stop.arrival_delay if kind == "arrival" else stop.departure_delay
                if stop.status != SCHEDULED or delay is None or not plausible(delay):
                    continue
                sec = scheduled[stop.stop_sequence - 1]
                day = filed_day(poll.captured_at, sec)
                if poll.captured_at > service_day_instant(day, sec) + latest:
                    continue
                out[(entry.trip_id, day, stop.stop_sequence)].append((_order(poll), delay))
    return out


def _last_plausible(scenario: Scenario, sent: Sequence[Poll]) -> dict[tuple[str, date, int], int]:
    """Per (trip, service date, stop sequence), the delay of its last
    plausible departure reading."""
    return {key: readings[-1][1] for key, readings in _plausible_readings(scenario, sent, "departure").items()}


def _model_delay(feed: FeedModel, run: TripRun, trip: ScheduledTrip, seq: int, captured_at: datetime) -> int:
    """The delay a poll captured at *captured_at* predicts stop *seq* of
    *run* at, under *feed*'s prediction model and freshness lag."""
    if feed.prediction == "perfect":
        actual = run.actual_sec[seq - 1]
        if actual is None:
            raise ValueError(f"{run.trip_id} {run.service_date} stop {seq} is not served")
        return actual - trip.scheduled_sec[seq - 1]
    known = captured_at - feed.lag
    left = [
        actual - scheduled
        for scheduled, actual in zip(trip.scheduled_sec, run.actual_sec, strict=True)
        if actual is not None and service_day_instant(run.service_date, actual) <= known
    ]
    return left[-1] if left else 0


def _last_listing(feed: FeedModel, departs: datetime) -> datetime:
    """The capture instant of the last poll to list a stop its run leaves at
    *departs*: the last multiple of the poll interval before the departure
    plus the freshness lag."""
    step = feed.poll_interval_sec
    edge = math.ceil((departs + feed.lag).timestamp() / step)
    return datetime.fromtimestamp((edge - 1) * step, timezone.utc)


def _check_model(scenario: Scenario, captured: Sequence[Poll]) -> None:
    """Raise unless every departure reading in *captured* carries the delay
    the scenario's feed model predicts at its poll's capture, and each
    served stop is last read in the last poll that lists it
    (``_last_listing``)."""
    runs = {(r.trip_id, r.service_date): r for r in scenario.runs}
    last_read: dict[tuple[str, date, int], datetime] = {}
    for poll in captured:
        for entry in poll.trips:
            run = runs[(entry.trip_id, entry.service_date)]
            trip = scenario.trip(run.trip_id, run.service_date)
            for stop in entry.stops:
                key = (entry.trip_id, entry.service_date, stop.stop_sequence)
                want = _model_delay(scenario.feed, run, trip, stop.stop_sequence, poll.captured_at)
                if stop.departure_delay != want:
                    raise ValueError(
                        f"{key} read at {poll.captured_at}: {stop.departure_delay}, not the model's {want}"
                    )
                last_read[key] = max(last_read.get(key, poll.captured_at), poll.captured_at)
    for run in scenario.runs:
        for seq, actual in enumerate(run.served, start=1):
            key = (run.trip_id, run.service_date, seq)
            want_at = _last_listing(scenario.feed, service_day_instant(run.service_date, actual))
            if last_read.get(key) != want_at:
                raise ValueError(f"{key} last read at {last_read.get(key)}, not at {want_at}")


def departures(scenario: Scenario, sent: Sequence[Poll], captured: Sequence[Poll]) -> list[Departure]:
    """Every served stop of every run with a plausible reading in *sent*,
    at that last reading's delay, but a run's last stop under a feed that
    reports arrivals. *sent* is the polls that reached history, of the
    *captured* polls.

    Raises when a run no misreport or relisting names was last reported in
    *captured* at a delay other than its true one, or a true delay inside the
    clamp went without a plausible reading in *captured*, and under a feed that
    predicts imperfectly, when a reading in *captured* is not its model's
    prediction or a stop is last read in another poll than the last to list
    it (``_check_model``): the feed model would then be at fault, not the
    pipeline. A departure *sent* lacks a reading of is one the path that
    brought its day never received."""
    last = _last_plausible(scenario, sent)
    feed_last = _last_plausible(scenario, captured)
    misreported = {(m.trip_id, m.service_date) for m in (*scenario.feed.misreports, *scenario.feed.relistings)}
    if scenario.feed.imperfect:
        _check_model(scenario, captured)
    out = []
    for run in scenario.runs:
        trip = scenario.trip(run.trip_id, run.service_date)
        stops = scenario.route(trip.route_id).stops
        for seq, (stop, scheduled, actual) in enumerate(
            zip(stops, trip.scheduled_sec, run.actual_sec, strict=True), start=1
        ):
            if actual is None or (scenario.feed.arrivals and seq == len(run.actual_sec)):
                continue
            key = (run.trip_id, run.service_date, seq)
            by_feed = feed_last.get(key)
            truthful = by_feed == actual - scheduled if plausible(actual - scheduled) else by_feed is None
            if not (truthful or scenario.feed.imperfect or (run.trip_id, run.service_date) in misreported):
                raise ValueError(f"{run.trip_id} {run.service_date} stop {seq}: reported {by_feed}, not its truth")
            reported = last.get(key)
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


@dataclass(frozen=True)
class Visit:
    """One trip run at one stop on its service date: its actual arrival and
    departure in seconds on the service date's clock, None for a side the
    feed left without a plausible reading, and whether the departure was
    reported at or after the arrival was first reported."""

    route_id: str
    trip_id: str
    service_date: date
    day_type: str
    stop_sequence: int
    arrival_sec: int | None
    departure_sec: int | None
    departure_observed: bool


def visits(scenario: Scenario, sent: Sequence[Poll], found: Iterable[Departure]) -> list[Visit]:
    """Every stop of every run with a departure among *found* or a plausible
    arrival reading in *sent*.

    Raises when an arrival was last reported plausibly at a delay other than
    its true one, or reported for a run that declares none: the feed model
    would then be at fault, not the pipeline."""
    arrivals = _plausible_readings(scenario, sent, "arrival")
    departure_readings = _plausible_readings(scenario, sent, "departure")
    departed = {(d.trip_id, d.service_date, d.stop_sequence): d.delay_sec for d in found}
    out = []
    for run in scenario.runs:
        trip = scenario.trip(run.trip_id, run.service_date)
        for i, (scheduled_arrival, scheduled) in enumerate(
            zip(trip.scheduled_arrival_sec, trip.scheduled_sec, strict=True)
        ):
            key = (run.trip_id, run.service_date, i + 1)
            departure_delay = departed.get(key)
            read = arrivals.get(key)
            arrival_delay = None if read is None else read[-1][1]
            if arrival_delay is not None and (
                run.arrival_sec is None or arrival_delay != run.arrival_sec[i] - scheduled_arrival
            ):
                raise ValueError(f"{run.trip_id} {run.service_date} stop {i + 1}: arrival reported {arrival_delay}")
            if departure_delay is None or read is None:
                observed = False
            else:
                observed = departure_readings[key][-1][0] >= read[0][0]
            if departure_delay is None and arrival_delay is None:
                continue
            out.append(
                Visit(
                    trip.route_id,
                    trip.trip_id,
                    run.service_date,
                    trip.day_type,
                    i + 1,
                    None if arrival_delay is None else scheduled_arrival + arrival_delay,
                    None if departure_delay is None else scheduled + departure_delay,
                    observed,
                )
            )
    return out


@dataclass(frozen=True)
class Prediction:
    """A departure beside its truth: the delay the run truly left the stop
    at, and the lead time of the reading the departure's delay is that of,
    in seconds."""

    departure: Departure
    true_delay_sec: int
    lead_sec: int

    @property
    def error_sec(self) -> int:
        return self.departure.delay_sec - self.true_delay_sec


def predictions(scenario: Scenario, sent: Sequence[Poll], found: Iterable[Departure]) -> list[Prediction]:
    """Each departure of *found* beside its truth, its lead time that of its
    last plausible reading in *sent*."""
    read = _plausible_readings(scenario, sent, "departure")
    runs = {(r.trip_id, r.service_date): r for r in scenario.runs}
    out = []
    for d in found:
        actual = runs[(d.trip_id, d.service_date)].actual_sec[d.stop_sequence - 1]
        if actual is None or d.scheduled_sec is None:
            raise ValueError(f"{d.trip_id} {d.service_date} stop {d.stop_sequence} has no true departure")
        (read_at, _file), _delay = read[(d.trip_id, d.service_date, d.stop_sequence)][-1]
        lead = service_day_instant(d.service_date, actual) - read_at
        out.append(Prediction(d, actual - d.scheduled_sec, lead // timedelta(seconds=1)))
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
    """The outcome the scenario's truth and the polls of its feed that reach
    history imply. Each service date runs the timetable version in force
    that day, and each version counts one trip per trip it defines, whatever
    days it runs."""
    captured = polls(scenario)
    arrived = received(scenario, captured)
    sent = [r.poll for r in arrived]
    found = tuple(departures(scenario, sent, captured))
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
        visits=tuple(visits(scenario, sent, found)),
        sources=sources(arrived),
        predictions=tuple(predictions(scenario, sent, found)),
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


def _days_between(first: date, last: date) -> list[date]:
    """Each day from *first* to *last*, both included."""
    return [first + timedelta(days=n) for n in range((last - first).days + 1)]


def readings_over(found: Outcome, first: date, last: date) -> Readings:
    """The readings of the service days *first* to *last*, summed."""
    days = [found.readings[day] for day in _days_between(first, last) if day in found.readings]
    return Readings(
        readings=sum(r.readings for r in days),
        implausible=sum(r.implausible for r in days),
        rows=sum(r.rows for r in days),
    )


@dataclass(frozen=True)
class SourceMix:
    """A period's service days by the source of their observations."""

    archive_days: int
    live_days: int
    none_days: int


def source_mix(found: Outcome, first: date, last: date) -> SourceMix:
    """The service days *first* to *last* by their source."""
    named = [found.sources[day].source if day in found.sources else None for day in _days_between(first, last)]
    return SourceMix(archive_days=named.count("archive"), live_days=named.count("live"), none_days=named.count(None))


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


@dataclass(frozen=True)
class Timing:
    """A visit's dwell and running time in seconds, None for one it has not."""

    visit: Visit
    dwell_sec: int | None
    running_sec: int | None


def timings(found: Outcome) -> list[Timing]:
    """Each visit's dwell and running time, the visit before it being the
    nearest earlier stop of its run with a visit."""
    by_run: dict[tuple[str, date], list[Visit]] = defaultdict(list)
    for v in found.visits:
        by_run[(v.trip_id, v.service_date)].append(v)
    out = []
    for run_visits in by_run.values():
        before: Visit | None = None
        for v in sorted(run_visits, key=lambda v: v.stop_sequence):
            dwell = None
            if v.departure_observed and v.arrival_sec is not None and v.departure_sec is not None:
                dwell = v.departure_sec - v.arrival_sec
            running = None
            if v.arrival_sec is not None and before is not None and before.departure_sec is not None:
                running = v.arrival_sec - before.departure_sec
            out.append(Timing(v, dwell, running))
            before = v
    return out


@dataclass(frozen=True)
class DwellRun:
    """The dwell and running samples of a set of visits, in seconds."""

    dwell: tuple[int, ...]
    running: tuple[int, ...]

    @property
    def dwell_mean_sec(self) -> Fraction | None:
        return Fraction(sum(self.dwell), len(self.dwell)) if self.dwell else None

    @property
    def running_mean_sec(self) -> Fraction | None:
        return Fraction(sum(self.running), len(self.running)) if self.running else None


def _dwell_run(group: Iterable[Timing]) -> DwellRun:
    group = list(group)
    return DwellRun(
        dwell=tuple(t.dwell_sec for t in group if t.dwell_sec is not None),
        running=tuple(t.running_sec for t in group if t.running_sec is not None),
    )


def dwell_run_by_route_day(found: Outcome) -> dict[tuple[str, date, str], DwellRun]:
    """Per (route, service date, day type) with a visit, its samples."""
    groups: dict[tuple[str, date, str], list[Timing]] = defaultdict(list)
    for t in timings(found):
        groups[(t.visit.route_id, t.visit.service_date, t.visit.day_type)].append(t)
    return {key: _dwell_run(group) for key, group in groups.items()}


def dwell_run_by_route(found: Outcome, first: date, last: date) -> dict[str, DwellRun]:
    """Per route with a visit on the service days *first* to *last*, its
    samples pooled over them, none when no visit pairs."""
    groups: dict[str, list[Timing]] = defaultdict(list)
    for t in timings(found):
        if first <= t.visit.service_date <= last:
            groups[t.visit.route_id].append(t)
    return {route: _dwell_run(group) for route, group in groups.items()}


@dataclass(frozen=True)
class PredictionError:
    """How far a set of departures' delays are from their truth: how many
    departures, and the sums of their errors and of their absolute errors,
    in seconds."""

    departures: int
    error_sum_sec: int
    abs_error_sum_sec: int

    @property
    def bias_sec(self) -> Fraction:
        return Fraction(self.error_sum_sec, self.departures)

    @property
    def mae_sec(self) -> Fraction:
        return Fraction(self.abs_error_sum_sec, self.departures)


def prediction_error(group: Iterable[Prediction]) -> PredictionError:
    """The error over *group*."""
    errors = [p.error_sec for p in group]
    if not errors:
        raise ValueError("no departures to measure")
    return PredictionError(len(errors), sum(errors), sum(abs(e) for e in errors))


def prediction_error_by_lead_time(found: Outcome, width_sec: int) -> dict[int, PredictionError]:
    """The error per lead-time group *width_sec* wide, keyed by its first
    second: a lead of l seconds falls in the group from the largest
    multiple of the width not above l."""
    if width_sec <= 0:
        raise ValueError("a lead-time group must be wider than nothing")
    groups: dict[int, list[Prediction]] = defaultdict(list)
    for p in found.predictions:
        groups[p.lead_sec // width_sec * width_sec].append(p)
    return {start: prediction_error(group) for start, group in sorted(groups.items())}
