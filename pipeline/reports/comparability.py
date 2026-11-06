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
can work out the verdict from the row itself.
"""

from __future__ import annotations

import math
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


def observation_coverage(
    ingest_strategy: str | None, observed_trips: int | None, planned_trips: int | None
) -> float | None:
    """Observed trips as a percentage of planned trips, over the days a
    timetable plans, to one decimal.

    Not capped at 100: more observed than planned means the feed reported
    trips the plan doesn't name, and that stays visible. ``None`` when it
    cannot be measured: a strategy whose trips cannot be matched to the
    timetable, no plan, or no observed count.
    """
    if (
        ingest_strategy not in COMPARABLE_INGEST_STRATEGIES
        or observed_trips is None
        or planned_trips is None
        or planned_trips <= 0
    ):
        return None
    return round(observed_trips / planned_trips * 100, 1)


def on_time_interval(
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


def assess(
    *,
    ingest_strategy: str | None,
    coverage_pct: float | None,
    ci_low: float | None,
    ci_high: float | None,
    clamp_pct: float | None,
    samples: int,
) -> tuple[bool, ComparableReason | None]:
    """``(comparable, reason)``: whether the board may rank this agency, and
    otherwise the first check it fails, in the module's order.

    Each threshold is inclusive: a figure exactly at it passes. A missing
    interval fails as too wide, since nothing bounds it. A missing clamp
    share passes: the share is absent only when the feed-health aggregate
    has no row, which analyze writes in the same run as the delay figures,
    so its absence says nothing about the feed's readings.
    """
    if samples <= 0:
        return (False, "no_data")
    if ingest_strategy not in COMPARABLE_INGEST_STRATEGIES or coverage_pct is None:
        return (False, "coverage_unknown")
    if coverage_pct < MIN_COVERAGE_PCT:
        return (False, "coverage_low")
    # Rounded as a reader would round the two printed bounds' difference, so
    # binary noise in the subtraction never flips a boundary case.
    if ci_low is None or ci_high is None or round((ci_high - ci_low) / 2, 2) > MAX_CI_HALF_WIDTH_PP:
        return (False, "ci_wide")
    if clamp_pct is not None and clamp_pct > MAX_CLAMP_PCT:
        return (False, "clamp_high")
    return (True, None)
