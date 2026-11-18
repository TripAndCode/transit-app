"""The route report card: each route's monthly grade (:mod:`pipeline.route_grade`)
with the figures behind it, computed at read time from the aggregates.

One computation covers every route of an agency for one calendar month, so a
route's card and the agency's list cannot disagree, and a card's earlier
months are the same computation for those months. It is cached per agency
and evicted whenever analyze rebuilds the agency's aggregates.

Each figure comes from the computation that already states it elsewhere, read
over the month's days under the industry on-time preset:

- the on-time and early-departure rates and the observation count from the
  Routes table (:func:`pipeline.reports.route_table.compute_route_table`);
- excess waiting time and bunching from the headway quality report
  (:func:`pipeline.reports.headway_quality.compute_headway_quality_report`),
  pooled over the days the route is high-frequency on;
- the service-delivered rate, planned trips and trips observed from the
  route's service-delivered reading
  (:func:`pipeline.reports.service_delivered.compute_service_delivered_by_route`),
  judged as the performance standard judges it
  (:func:`pipeline.reports.performance_standard.delivered_reading`).

A route gets no letter, in this order, when the feed observed it on no day
(``no_data``), reports zero delay for it only (``zero_delay_feed``), observed
it fewer than :data:`MIN_OBSERVATIONS` times (``too_few_observations``), or
when the comparability gate's coverage and interval checks fail on the
route's own trips (``coverage_unknown``, ``coverage_low``, ``ci_wide``,
:func:`pipeline.reports.comparability.assess`). The gate's clamp check reads
an agency-wide figure, so it is not a route's verdict and is left out. A route
that passes all of these and still lacks a figure its grade needs is
``not_judged``, naming each missing figure with its reason.

The floor is the Routes table's ranking floor, the one the performance
standard judges a route's on-time and service-delivered figures at: a grade is
a judgement on the route, as a standard is.

Its frequency class is read from the timetable in force on each day of the
month (``agg_route_headway_daily.is_high_frequency``): high-frequency when it
is so on at least half the days judged. A route with no day judged takes the
loaded timetable's class (``agg_route_headway``), and a route the timetable
gives no headway is low-frequency, as absence of a schedule is no evidence of
frequent service. The class never comes from whether headway figures exist,
so a high-frequency route whose feed cannot give them is not judged rather
than graded on punctuality.

Intervals are 95% Wilson intervals with the route's trips as the sample size
(:func:`pipeline.reports.comparability.trip_interval`): stop events move
together within a trip, so counting them would claim an interval several times
too narrow. Excess waiting time has no interval: it is a difference of two
length-biased means, and the pooled sums it comes from cannot give its spread.
Bunching has none either: it pools only the days the route is high-frequency
on and of its dominant day type, and the trips behind those days are not
counted apart from the month's, so the month's trips would claim an interval
narrower than the figure supports.
"""

from __future__ import annotations

from calendar import monthrange
from dataclasses import dataclass
from datetime import date
from typing import Any, Literal

from api.range import RangeCtx
from pipeline import perf
from pipeline.cache import async_lru_cache
from pipeline.reports.comparability import Assessment, assess, trip_interval
from pipeline.reports.headway_quality import compute_headway_quality_report
from pipeline.reports.performance_standard import delivered_reading
from pipeline.reports.rankings import DEFAULT_EARLY_TOLERANCE_SEC, DEFAULT_LATE_TOLERANCE_SEC, RANKING_MIN_SAMPLES
from pipeline.reports.route_table import compute_route_table
from pipeline.reports.service_delivered import RouteDelivered, compute_service_delivered_by_route
from pipeline.route_catalog import zero_delay_route_codes
from pipeline.route_grade import CLASS_METRICS, FrequencyClass, Grade, Metric, grade_route, judged

#: The fewest observations behind a graded route.
MIN_OBSERVATIONS = RANKING_MIN_SAMPLES

#: The order the card lists its figures in.
SUBSCORE_ORDER: tuple[Metric, ...] = ("on_time_pct", "early_pct", "ewt_min", "bunching_pct", "service_delivered_pct")

#: How many months a card's trend shows, its own included.
HISTORY_MONTHS = 3

NotGradedReason = Literal[
    "no_data", "zero_delay_feed", "too_few_observations", "coverage_unknown", "coverage_low", "ci_wide", "not_judged"
]

# Each route's days judged high-frequency and not, by the timetable in force.
_DAILY_CLASS_SQL = """
    SELECT route_code,
           count(*) FILTER (WHERE is_high_frequency) AS high,
           count(*) FILTER (WHERE is_high_frequency = false) AS low
    FROM agg_route_headway_daily
    WHERE agency_id = $1 AND date BETWEEN $2 AND $3
    GROUP BY route_code
"""

_LOADED_CLASS_SQL = "SELECT route_code, is_high_frequency FROM agg_route_headway WHERE agency_id = $1"


def month_bounds(month: date) -> tuple[date, date]:
    """The first and last day of *month*'s calendar month."""
    return month.replace(day=1), month.replace(day=monthrange(month.year, month.month)[1])


def latest_closed_month(last_closed_day: date) -> date:
    """The newest calendar month every day of which has closed by *last_closed_day*."""
    first, last = month_bounds(last_closed_day)
    if last_closed_day == last:
        return first
    return _shift(first, -1)


def _shift(month: date, months: int) -> date:
    index = month.year * 12 + month.month - 1 + months
    return date(index // 12, index % 12 + 1, 1)


def history_months(month: date) -> list[date]:
    """*month* and the :data:`HISTORY_MONTHS` - 1 before it, oldest first."""
    return [_shift(month.replace(day=1), -k) for k in range(HISTORY_MONTHS - 1, -1, -1)]


@dataclass(frozen=True)
class SubScore:
    """One figure on the card. ``value`` is None exactly when ``missing``
    says why. ``letter`` is its band, or for the service-delivered rate the
    best letter it allows; ``graded`` whether the route's class is graded on
    it. Rates are in percent, excess wait in minutes."""

    metric: Metric
    value: float | None
    ci_low: float | None
    ci_high: float | None
    letter: Grade | None
    graded: bool
    missing: str | None


@dataclass(frozen=True)
class RouteCard:
    route_code: str
    month: date
    frequency: FrequencyClass
    grade: Grade | None
    not_graded: NotGradedReason | None
    # The figures behind the verdict: observations against the floor, the
    # route's trips observed against planned, and its coverage and interval
    # as the gate read them.
    observations: int
    floor: int
    trips_observed: int | None
    planned_trips: int | None
    coverage_pct: float | None
    on_time_ci_low: float | None
    on_time_ci_high: float | None
    missing: tuple[tuple[Metric, str], ...]
    subscores: tuple[SubScore, ...]
    raise_to: tuple[tuple[Metric, float, float], ...]


async def _frequency_classes(conn, agency_id: int, first: date, last: date) -> dict[str, FrequencyClass]:
    daily = {r["route_code"]: (r["high"], r["low"]) for r in await conn.fetch(_DAILY_CLASS_SQL, agency_id, first, last)}
    loaded = {r["route_code"]: r["is_high_frequency"] for r in await conn.fetch(_LOADED_CLASS_SQL, agency_id)}
    classes: dict[str, FrequencyClass] = {}
    for code in daily.keys() | loaded.keys():
        high, low = daily.get(code, (0, 0))
        if high or low:
            classes[code] = "high" if high >= low else "low"
        else:
            classes[code] = "high" if loaded.get(code) else "low"
    return classes


@perf.timed("reports.report_cards")
@async_lru_cache(maxsize=64, ttl_seconds=300, agency_arg="agency_id")
async def compute_report_cards(agency_id: int, month: date, conn) -> dict[str, RouteCard]:
    """Every route's card for the calendar month holding *month*: each route
    the feed observed that month or the timetable planned for it."""
    first, last = month_bounds(month)
    ctx = RangeCtx(from_date=first, to_date=last)
    strategy = await conn.fetchval("SELECT ingest_strategy FROM agencies WHERE agency_id = $1", agency_id)
    table = await compute_route_table(
        agency_id,
        ctx,
        conn,
        min_samples=1,
        early_tolerance_sec=DEFAULT_EARLY_TOLERANCE_SEC,
        late_tolerance_sec=DEFAULT_LATE_TOLERANCE_SEC,
    )
    observed = {row[0]: (float(row[2]), int(row[5]), float(row[6])) for row in table}
    delivered = await compute_service_delivered_by_route(conn, agency_id, first, last)
    zero_delay = await zero_delay_route_codes(conn, agency_id, ctx)
    headways = await compute_headway_quality_report(agency_id, ctx, conn)
    quality = {row["route_code"]: row for row in headways.rows}
    headway_gap = headways.unavailable.get("unavailable_reason")
    classes = await _frequency_classes(conn, agency_id, first, last)

    planned_codes = {code for code, r in delivered.routes.items() if r.planned_trips > 0}
    codes = sorted((observed.keys() | planned_codes) - {""})
    cards: dict[str, RouteCard] = {}
    for code in codes:
        on_time_pct, samples, early_pct = observed.get(code, (None, 0, None))
        route = delivered.routes.get(code)
        split = delivered.split_known and route is not None
        trips = route.trips_observed if split and route is not None else None
        planned = route.planned_trips if split and route is not None else None
        gate = assess(
            ingest_strategy=strategy,
            observed_trips=trips,
            coverage_planned_trips=planned,
            on_time_count=round((on_time_pct or 0.0) * samples / 100),
            samples=samples,
            clamp_pct=None,
        )
        delivered_pct, delivered_why = delivered_reading(
            delivered.delivered_missing, delivered.split_known, route, zero_delay=code in zero_delay
        )
        frequency = classes.get(code, "low")
        figures, reasons = _figures(
            on_time_pct, early_pct, quality.get(code), headway_gap, delivered_pct, delivered_why
        )

        not_graded: NotGradedReason | None
        if samples == 0:
            not_graded = "no_data"
        elif code in zero_delay:
            not_graded = "zero_delay_feed"
        elif samples < MIN_OBSERVATIONS:
            not_graded = "too_few_observations"
        elif gate.comparable_reason in ("coverage_unknown", "coverage_low", "ci_wide"):
            not_graded = gate.comparable_reason
        else:
            not_graded = None
        verdict = grade_route(frequency, figures, reasons)
        if not_graded is None and verdict.grade is None:
            not_graded = "not_judged"
        # A route a gate stops has no letter anywhere on its card: a letter
        # beside its figures would grade what the gate says cannot be graded.
        letters = verdict.letters if not_graded in (None, "not_judged") else {}
        subscores = _subscores(frequency, figures, reasons, letters, gate, trips, route)
        graded = not_graded is None
        cards[code] = RouteCard(
            route_code=code,
            month=first,
            frequency=frequency,
            grade=verdict.grade if graded else None,
            not_graded=not_graded,
            observations=samples,
            floor=MIN_OBSERVATIONS,
            trips_observed=gate.observed_trips,
            planned_trips=gate.coverage_planned_trips,
            coverage_pct=gate.coverage_pct,
            on_time_ci_low=gate.ci_low,
            on_time_ci_high=gate.ci_high,
            missing=verdict.missing if not_graded == "not_judged" else (),
            subscores=subscores,
            raise_to=tuple((t.metric, t.value, t.target) for t in verdict.raise_to) if graded else (),
        )
    return cards


def _figures(
    on_time_pct: float | None,
    early_pct: float | None,
    headway: dict[str, Any] | None,
    headway_gap: str | None,
    delivered_pct: float | None,
    delivered_why: dict[str, Any] | None,
) -> tuple[dict[Metric, float | None], dict[Metric, str]]:
    """Each criterion's figure, and why each missing one is missing."""
    ewt_sec = None if headway is None else headway["ewt_sec"]
    bunching = None if headway is None else headway["bunching_rate"]
    figures: dict[Metric, float | None] = {
        "on_time_pct": on_time_pct,
        "early_pct": early_pct,
        "ewt_min": None if ewt_sec is None else ewt_sec / 60,
        "bunching_pct": None if bunching is None else bunching * 100,
        "service_delivered_pct": delivered_pct,
    }
    headway_reason = headway_gap or "not_measured"
    reasons: dict[Metric, str] = {
        "on_time_pct": "no_data",
        "early_pct": "no_data",
        "ewt_min": headway_reason,
        "bunching_pct": headway_reason,
        "service_delivered_pct": (delivered_why or {}).get("reason", "not_measured"),
    }
    return figures, reasons


def _subscores(
    frequency: FrequencyClass,
    figures: dict[Metric, float | None],
    reasons: dict[Metric, str],
    letters: dict[Metric, Grade],
    gate: Assessment,
    trips: int | None,
    route: RouteDelivered | None,
) -> tuple[SubScore, ...]:
    """Every figure on the card, with its interval and its letter from
    *letters* (:func:`pipeline.route_grade.grade_route`), whether or not the
    route's class is graded on it."""
    graded = {*CLASS_METRICS[frequency], "service_delivered_pct"}
    early = figures["early_pct"]
    delivered = figures["service_delivered_pct"]
    intervals: dict[Metric, tuple[float | None, float | None]] = {
        "on_time_pct": (gate.ci_low, gate.ci_high),
        "early_pct": (None, None) if early is None else trip_interval(early, 100, trips),
        "ewt_min": (None, None),
        "bunching_pct": (None, None),
        "service_delivered_pct": (
            (None, None) if delivered is None or route is None else trip_interval(delivered, 100, route.planned_trips)
        ),
    }
    out = []
    for metric in SUBSCORE_ORDER:
        value = figures[metric]
        low, high = intervals[metric]
        out.append(
            SubScore(
                metric=metric,
                value=None if value is None else judged(value),
                ci_low=low,
                ci_high=high,
                letter=letters.get(metric),
                graded=metric in graded,
                missing=None if value is not None else reasons[metric],
            )
        )
    return tuple(out)
