"""Whether the cross-agency board may rank an agency against the others.

A ranking is an action for the council that reads it, so the board ranks only
the agencies whose data supports the comparison and lists the rest unranked,
each with the first check it fails. The checks run in a fixed order, so one
agency always gives the same reason:

1. ``no_data``: no observation in the period.
2. ``coverage_unknown``: observation coverage cannot be measured, because the
   ingest strategy's trip_ids cannot be matched to the timetable's, or no
   plan covers the days observed, or the plan could not be read.
3. ``coverage_low``: the feed reported too few of the planned trips.
4. ``ci_wide``: the on-time percentage is too uncertain to order.
5. ``clamp_high``: too many readings were implausible.

Each check reads the figure the board prints, rounded as printed, so a reader
can work out the verdict from the row itself. :func:`assess` is the gate's
one entry: it decides which counts may be used at all before it computes and
judges the figures, so no caller can judge a figure the gate would not print.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Literal

from pipeline.stats import wilson_interval

ComparableReason = Literal["no_data", "coverage_unknown", "coverage_low", "ci_wide", "clamp_high"]

#: Below this share of planned trips observed, the feed's gaps could hide any
#: delay pattern, and their bias differs feed by feed.
MIN_COVERAGE_PCT = 70.0

#: Wider than this either side, an interval spans more than the gap between
#: many neighbouring agencies' on-time rates, so ranking them would order noise.
MAX_CI_HALF_WIDTH_PP = 3.0

#: Above this share of implausible readings, the feed is producing errors
#: often enough that the readings it kept are suspect too.
MAX_CLAMP_PCT = 5.0

#: Only these strategies ingest trip_ids from the timetable they join, so
#: their observed trips can be counted against its planned ones.
#: ``aomori_regex`` parses its trip_ids out of the feed's own text, which
#: does not match the timetable's, so its coverage cannot be measured. This
#: is a claim about trip matching, separate from which strategies can carry
#: cancellations, though both hold only for ``static_join`` today.
COMPARABLE_INGEST_STRATEGIES = frozenset({"static_join"})


@dataclass(frozen=True)
class Assessment:
    """One agency's gate figures, as the board prints them, and its verdict.

    ``observed_trips`` and ``coverage_planned_trips`` are ``None`` for a
    strategy whose trips cannot be matched to the timetable, so nothing
    downstream can count them.
    """

    observed_trips: int | None
    coverage_planned_trips: int | None
    coverage_pct: float | None
    ci_low: float | None
    ci_high: float | None
    comparable: bool
    comparable_reason: ComparableReason | None


def assess(
    *,
    ingest_strategy: str | None,
    observed_trips: int | None,
    coverage_planned_trips: int | None,
    on_time_count: int,
    samples: int,
    clamp_pct: float | None,
) -> Assessment:
    """Whether the board may rank this agency, with every figure the verdict
    reads.

    ``observed_trips`` of ``coverage_planned_trips`` are the trips the feed
    reported and the timetable planned over the same days
    (:func:`pipeline.reports.service_delivered.compute_service_delivered_by_agency`);
    ``on_time_count`` of ``samples`` the stop events on time and observed.
    """
    matchable = ingest_strategy in COMPARABLE_INGEST_STRATEGIES
    observed = observed_trips if matchable else None
    planned = coverage_planned_trips if matchable else None
    coverage_pct = _coverage_pct(observed, planned)
    ci_low, ci_high = _on_time_interval(on_time_count, samples, observed)
    reason = _first_failure(
        coverage_pct=coverage_pct, ci_low=ci_low, ci_high=ci_high, clamp_pct=clamp_pct, samples=samples
    )
    return Assessment(
        observed_trips=observed,
        coverage_planned_trips=planned,
        coverage_pct=coverage_pct,
        ci_low=ci_low,
        ci_high=ci_high,
        comparable=reason is None,
        comparable_reason=reason,
    )


def _coverage_pct(observed_trips: int | None, planned_trips: int | None) -> float | None:
    """Observed trips as a percentage of planned trips, to one decimal.

    Not capped at 100: more observed than planned means the feed reported
    trips the plan doesn't name, and that stays visible. ``None`` without a
    plan or an observed count.
    """
    if observed_trips is None or planned_trips is None or planned_trips <= 0:
        return None
    return round(observed_trips / planned_trips * 100, 1)


def _on_time_interval(
    on_time_count: int, samples: int, observed_trips: int | None
) -> tuple[float, float] | tuple[None, None]:
    """The on-time percentage's 95% Wilson interval, in percent.

    Stop events within one trip run late together, so they are not
    independent draws: the interval takes the stop-event share as the
    proportion but counts trips as the sample size. Counting stop events
    would claim an interval several times too narrow. The bounds round
    outward to one decimal, so the printed interval never reads narrower
    than the computed one.

    ``(None, None)`` without samples or a trip count. Falling back to the
    stop-event count would print exactly the overconfident interval this
    avoids.
    """
    if samples <= 0 or observed_trips is None or observed_trips <= 0:
        return (None, None)
    successes = round(on_time_count / samples * observed_trips)
    low, high = wilson_interval(successes, observed_trips)
    # The inner round drops float noise, so an exact tenth stays put.
    return (math.floor(round(low * 1000, 9)) / 10, math.ceil(round(high * 1000, 9)) / 10)


def tenth_as_printed(value: float) -> float:
    """``value`` to a tenth as the board prints it (``roundTo`` in
    frontend/src/utils/format.ts): the binary value times ten, rounded to the
    nearest whole number with ties away from zero. Python's ``round`` would
    differ on a tie such as 2.35."""
    scaled = abs(value) * 10
    whole = math.floor(scaled)
    if scaled - whole >= 0.5:
        whole += 1
    return math.copysign(whole / 10, value) if whole else 0.0


def ci_half_width_as_printed(ci_low: float, ci_high: float) -> float:
    """Half the interval's width as the board prints it (``ciHalfWidth`` in
    frontend/src/components/comparabilityReason.ts): rounded up to a tenth,
    so an interval the gate found wider than its limit never prints at the
    limit. The inner round drops float noise from the subtraction."""
    return math.ceil(round((ci_high - ci_low) / 2 * 1000) / 100) / 10


def _first_failure(
    *,
    coverage_pct: float | None,
    ci_low: float | None,
    ci_high: float | None,
    clamp_pct: float | None,
    samples: int,
) -> ComparableReason | None:
    """The first check the figures fail, in the module's order, or ``None``.

    Each threshold is inclusive: a figure printed exactly at it passes. A
    missing interval fails as too wide, since nothing bounds it. A missing
    clamp share passes: the share is absent only when the feed-health
    aggregate has no row, which analyze writes in the same run as the delay
    figures, so its absence says nothing about the feed's readings.
    """
    if samples <= 0:
        return "no_data"
    if coverage_pct is None:
        return "coverage_unknown"
    if coverage_pct < MIN_COVERAGE_PCT:
        return "coverage_low"
    # Rounded as a reader would round the two printed bounds' difference, so
    # binary noise in the subtraction never flips a boundary case.
    if ci_low is None or ci_high is None or round((ci_high - ci_low) / 2, 2) > MAX_CI_HALF_WIDTH_PP:
        return "ci_wide"
    if clamp_pct is not None and tenth_as_printed(clamp_pct) > MAX_CLAMP_PCT:
        return "clamp_high"
    return None
