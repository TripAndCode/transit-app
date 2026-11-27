"""The route report card's monthly grade, A to F, from one route's figures
over a calendar month (:mod:`pipeline.reports.report_card`).

The grade is recomputed under the rules below whenever it is read, so a rule
change moves no aggregate and is not a new method version. Every edge is
inclusive: a figure printed exactly at an edge earns the better letter. Each
figure is judged as it is printed, to a tenth (:func:`judged`), so a reader
can confirm the letter from the card itself. The rules are this app's
defaults for every agency; per-agency rules are not configurable.

A route is judged by its frequency class, the existing high-frequency rule
(:data:`pipeline.headways.HIGH_FREQUENCY_HEADWAY_SEC`, a scheduled headway of
12 minutes or less):

- High-frequency: passengers turn up without a timetable, so what matters is
  the wait. Excess waiting time (:data:`EWT_MIN_EDGES`) and bunching
  (:data:`BUNCHING_PCT_EDGES`) are each given a letter and the route takes the
  worse, so an F on either is an F. Bus Turnaround (busturnaround.nyc/
  report-cards) gives the bunching definition, a bus arriving less than a
  quarter of the scheduled headway after the one before, and its A edge, 5% or
  less on a frequent route. The excess-wait edges are this app's defaults;
  TfL's high-frequency target of 1.1 minutes (tfl.gov.uk, buses performance
  data) falls in band C. The B, C and D edges split the A to F span evenly.
- Low-frequency: passengers time their arrival to the timetable, so what
  matters is keeping it. The on-time rate under the industry preset
  ``standard_m1_p5`` (:data:`ON_TIME_PCT_EDGES`) and the early-departure rate
  (:data:`EARLY_PCT_EDGES`) are each given a letter and the route takes the
  worse. The A and F edges are this app's defaults; Bus Turnaround's target of
  75% on time falls in band C and TfL's 86% in band B. The early-departure rate
  is graded on its own as well as inside the on-time rate, on purpose: a bus
  that leaves early makes a passenger who arrived on time wait a whole headway,
  the worst outcome on a route that runs rarely, and the on-time rate alone
  counts it as one more miss beside lateness. Its edges are this app's own; no
  public scheme grades the rate separately.

Truancy, the service-delivered rate, then caps the letter whatever the class,
adapted from Bus Turnaround's rule that fails a route dropping too many trips:
below :data:`TRUANCY_CAP_PCT` the
letter is at best D, and below :data:`TRUANCY_FAIL_PCT` it is F.

A route without a figure a criterion needs has no letter, as the feed-quality
grade has none (:mod:`pipeline.feed_quality_grade`): the criterion is named
with the reason its figure is missing, never treated as passing. That includes
a feed that never reports a trip not run, which has no service-delivered rate.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from pipeline.reports.comparability import tenth_as_printed

Grade = Literal["A", "B", "C", "D", "F"]
GRADES: tuple[Grade, ...] = ("A", "B", "C", "D", "F")

Metric = Literal["ewt_min", "bunching_pct", "on_time_pct", "early_pct", "service_delivered_pct"]
FrequencyClass = Literal["high", "low"]

#: The edges of A, B, C and D, best first: a figure at or under an edge (at or
#: over, for a rate that is better higher) earns that letter, and one past D's
#: earns F.
EWT_MIN_EDGES: tuple[float, float, float, float] = (0.5, 1.0, 1.5, 2.0)
BUNCHING_PCT_EDGES: tuple[float, float, float, float] = (5.0, 10.0, 15.0, 20.0)
ON_TIME_PCT_EDGES: tuple[float, float, float, float] = (90.0, 80.0, 70.0, 60.0)
EARLY_PCT_EDGES: tuple[float, float, float, float] = (1.0, 2.0, 5.0, 10.0)

#: Below this service-delivered rate the letter is at best D.
TRUANCY_CAP_PCT = 95.0
#: Below this it is F.
TRUANCY_FAIL_PCT = 90.0

_EDGES: dict[Metric, tuple[float, float, float, float]] = {
    "ewt_min": EWT_MIN_EDGES,
    "bunching_pct": BUNCHING_PCT_EDGES,
    "on_time_pct": ON_TIME_PCT_EDGES,
    "early_pct": EARLY_PCT_EDGES,
}
_HIGHER_IS_BETTER: frozenset[Metric] = frozenset({"on_time_pct", "service_delivered_pct"})

#: The criteria each class is graded on, before truancy.
CLASS_METRICS: dict[FrequencyClass, tuple[Metric, Metric]] = {
    "high": ("ewt_min", "bunching_pct"),
    "low": ("on_time_pct", "early_pct"),
}


def judged(value: float) -> float:
    """*value* as the card prints it, to a tenth with ties away from zero."""
    return tenth_as_printed(value)


def band(metric: Metric, value: float) -> Grade:
    """The letter *value* earns on *metric*'s edges."""
    figure = judged(value)
    for grade, edge in zip(GRADES, _EDGES[metric], strict=False):
        if (figure >= edge) if metric in _HIGHER_IS_BETTER else (figure <= edge):
            return grade
    return "F"


def truancy_cap(service_delivered_pct: float) -> Grade:
    """The best letter a service-delivered rate allows: A when it is not
    truant, D below :data:`TRUANCY_CAP_PCT`, F below :data:`TRUANCY_FAIL_PCT`."""
    figure = judged(service_delivered_pct)
    if figure < TRUANCY_FAIL_PCT:
        return "F"
    if figure < TRUANCY_CAP_PCT:
        return "D"
    return "A"


def worse(*grades: Grade) -> Grade:
    return max(grades, key=GRADES.index)


@dataclass(frozen=True)
class Target:
    """What *metric* must reach for the next letter: from *value* to *target*."""

    metric: Metric
    value: float
    target: float


@dataclass(frozen=True)
class RouteGrade:
    """``grade`` is None exactly when ``missing`` names a criterion, each with
    the reason its figure is missing. ``letters`` gives every figure given its
    own letter, graded on or not (for truancy, the best letter it allows): the
    one source of the letters a card shows. ``raise_to``
    lists, for a letter below A, every figure that must move for the next
    letter up, and how far."""

    frequency: FrequencyClass
    grade: Grade | None
    letters: dict[Metric, Grade]
    missing: tuple[tuple[Metric, str], ...]
    raise_to: tuple[Target, ...]


def _edge_for(metric: Metric, grade: Grade) -> float:
    if metric == "service_delivered_pct":
        return TRUANCY_FAIL_PCT if grade == "D" else TRUANCY_CAP_PCT
    return _EDGES[metric][GRADES.index(grade)]


def grade_route(
    frequency: FrequencyClass,
    figures: dict[Metric, float | None],
    missing_reasons: dict[Metric, str],
) -> RouteGrade:
    """The grade of one route-month from *figures*: its class's criteria and
    ``service_delivered_pct``, each a figure or None with its reason in
    *missing_reasons* (``not_measured`` when none is given), and any other
    figure the card shows, lettered but not graded on."""
    criteria: tuple[Metric, ...] = (*CLASS_METRICS[frequency], "service_delivered_pct")
    letters: dict[Metric, Grade] = {
        metric: truancy_cap(value) if metric == "service_delivered_pct" else band(metric, value)
        for metric, value in figures.items()
        if value is not None
    }
    missing = [
        (metric, missing_reasons.get(metric, "not_measured")) for metric in criteria if figures.get(metric) is None
    ]
    if missing:
        return RouteGrade(frequency, None, letters, tuple(missing), ())
    grade = worse(*(letters[metric] for metric in criteria))
    raise_to: tuple[Target, ...] = ()
    if grade != "A":
        better = GRADES[GRADES.index(grade) - 1]
        raise_to = tuple(
            Target(metric, judged(value), _edge_for(metric, better))
            for metric in criteria
            if GRADES.index(letters[metric]) > GRADES.index(better)
            for value in (figures[metric],)
            if value is not None
        )
    return RouteGrade(frequency, grade, letters, (), raise_to)
