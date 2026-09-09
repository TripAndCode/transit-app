"""Headway reconstruction, high-frequency route classification, and headway
QUALITY metrics (Excess Waiting Time, coefficient of variation, long-gap rate).

A route's "headway" is the time gap between two consecutive vehicles serving
the same stop on the same route. This module has three independent pieces:

- Reconstructing ACTUAL headways from GTFS-RT observations, keyed by the
  physical stop (`updates.stop_id`, not `stop_sequence` -- a looping or
  branching route can visit the same physical stop at more than one
  `stop_sequence`, and grouping by `stop_sequence` instead would treat those
  as different stops and miss the true gap between vehicles that actually
  call at the same platform). `pipeline.analyze`'s `agg_route_headway_daily`
  builder materializes this for any ingest strategy that CAN populate
  `stop_id` (today: `static_join`; `aomori_regex` always leaves it NULL),
  which is necessary but not sufficient trust; `pipeline.reports.
  headway_quality` additionally gates on the per-agency confirmed-set check
  (`pipeline.strategies.static_join.rt_field_coverage_confirmed`) before
  reading these rows -- see that module's docstring for why sharing the
  wire shape doesn't by itself confirm a given agency's feed populates
  `stop_id`.
- Deriving the SCHEDULED headway median from the static GTFS `stop_times`
  table (`static_stop_times` in Postgres), independent of any RT data, used
  to classify a route as "high-frequency".
- Quality metrics computed FROM a headway distribution (actual or
  scheduled) -- mean wait time, coefficient of variation, and long-gap
  rate -- restricted to routes the second piece classifies high-frequency
  (see `pipeline.reports.headway_quality`). Each has both a direct-from-
  samples form (for a single in-memory gap list, e.g. in a unit test) and a
  from-pooled-statistics form (for combining multiple days' worth of gaps
  already reduced to sufficient statistics in Postgres, without re-fetching
  every raw gap) -- see `mean_wait_sec`/`mean_wait_from_pooled` and
  `coefficient_of_variation`/`coefficient_of_variation_from_pooled`.

Neither of the first two depends on the other: the scheduled classification
can be computed the moment a static feed is loaded, while actual-headway
reconstruction needs live RT history to have accumulated. The quality
metrics depend on both: they compare an agency's actual headways against
its own scheduled ones, and only mean anything for a route already
classified high-frequency.
"""

from __future__ import annotations

from itertools import pairwise
from statistics import median as _median
from typing import Sequence

# A route qualifies as "high-frequency" when passengers can reasonably show
# up without consulting a timetable. The literature's usual cutoff for a
# "frequent network" spans roughly 10-12 minutes; this picks the loose
# (upper) end of that band so a route sitting anywhere in it still counts,
# rather than arbitrarily excluding one end.
HIGH_FREQUENCY_HEADWAY_SEC = 12 * 60

# A gap this many times the scheduled headway counts as "long" -- a
# passenger waiting through one sees roughly double (or more) their
# expected wait, worth flagging distinctly from ordinary headway noise.
# Picks the middle of the commonly used 1.5x-2x band rather than either
# edge.
LONG_GAP_MULTIPLIER = 1.75


def reconstruct_headways(event_times_sec: Sequence[float]) -> list[float]:
    """Return the consecutive gaps between sorted *event_times_sec*.

    *event_times_sec* is one "actual" (or scheduled) event time in seconds,
    on any consistent origin, per vehicle visiting ONE physical stop on ONE
    route within one service day -- grouping to that granularity is the
    caller's responsibility; this function only sorts and diffs. Fewer than
    two events yields no headway (there is nothing to measure a gap
    between). A duplicate/degenerate timestamp produces a zero-second gap
    rather than being silently dropped -- callers that consider a zero gap
    non-informative (e.g. a data-quality duplicate) should filter it out
    themselves.
    """
    ordered = sorted(event_times_sec)
    return [b - a for a, b in pairwise(ordered)]


def headway_median_sec(event_times_sec: Sequence[float]) -> float | None:
    """Median of the consecutive gaps in *event_times_sec*.

    Returns ``None`` when fewer than two events are present -- there is
    nothing to compute a gap from, which must never be silently reported as
    a zero or otherwise fabricated headway.
    """
    gaps = reconstruct_headways(event_times_sec)
    if not gaps:
        return None
    return _median(gaps)


def is_high_frequency(scheduled_headway_median_sec: float | None) -> bool:
    """True iff *scheduled_headway_median_sec* is known and at or under the
    high-frequency cutoff.

    ``None`` (no scheduled data to derive a median from) is never
    high-frequency -- absence of a schedule is not evidence of frequent
    service.
    """
    return scheduled_headway_median_sec is not None and scheduled_headway_median_sec <= HIGH_FREQUENCY_HEADWAY_SEC


def mean_wait_from_moments(mean_headway_sec: float | None, mean_headway_sq_sec2: float | None) -> float | None:
    """Mean wait time for a passenger arriving uniformly at random, given the
    first and second moment (E[H], E[H^2]) of a headway distribution.

    This is the standard renewal-process formula E[H^2] / (2 * E[H]) --
    NOT headway/2. A passenger is more likely to land inside a long gap
    than a short one (length-biased sampling), so variability in the
    headways itself raises the expected wait even when the mean headway is
    unchanged; a perfectly regular headway (E[H^2] == E[H]^2) reduces this
    to the familiar headway/2.

    ``None`` when the mean headway is unknown or non-positive (division
    undefined/meaningless) or the second moment is unknown.
    """
    if not mean_headway_sec or mean_headway_sec <= 0 or mean_headway_sq_sec2 is None:
        return None
    return mean_headway_sq_sec2 / (2 * mean_headway_sec)


def mean_wait_sec(gaps: Sequence[float]) -> float | None:
    """Mean wait time computed directly from a sequence of headway *gaps*
    (see `mean_wait_from_moments`). ``None`` for an empty sequence."""
    if not gaps:
        return None
    n = len(gaps)
    return mean_wait_from_moments(sum(gaps) / n, sum(g * g for g in gaps) / n)


def mean_wait_from_pooled(n: int, sum_sec: float | None, sumsq_sec2: float | None) -> float | None:
    """Mean wait time from pooled sufficient statistics -- *n* headway
    samples, their sum, and their sum of squares. `sum`/`sumsq` are
    additive across any partition of the same gap population (e.g. summing
    each day's own (n, sum, sumsq) triple gives the same triple as
    computing directly over the days' concatenated gaps), so this is what a
    caller pooling multiple days of `agg_route_headway_daily` should use
    instead of re-deriving E[H]/E[H^2] from raw gaps it no longer has.
    ``None`` when there are no samples, or *sum_sec*/*sumsq_sec2* is
    ``None`` -- a row whose `actual_samples` predates the sufficient-
    statistics columns being added has a non-null sample count but null
    sums, and that must surface as an unresolved metric, not a crash.
    """
    if n <= 0 or sum_sec is None or sumsq_sec2 is None:
        return None
    return mean_wait_from_moments(sum_sec / n, sumsq_sec2 / n)


def excess_wait_time_sec(actual_gaps: Sequence[float], scheduled_gaps: Sequence[float]) -> float | None:
    """Excess Waiting Time: actual mean wait minus scheduled mean wait,
    both computed via `mean_wait_sec` from their respective reconstructed
    headway sequences (RT-observed vs. static-schedule-derived).

    ``None`` when either side has no resolvable mean wait (empty gaps).
    A perfectly on-schedule, perfectly regular actual service (same gaps as
    scheduled) yields exactly 0.0, not merely "close to zero".
    """
    actual = mean_wait_sec(actual_gaps)
    scheduled = mean_wait_sec(scheduled_gaps)
    if actual is None or scheduled is None:
        return None
    return actual - scheduled


def coefficient_of_variation(gaps: Sequence[float]) -> float | None:
    """Population coefficient of variation (stdev / mean) of *gaps* -- a
    scale-free measure of how bunched/irregular the headways are (0 for
    perfectly even spacing, larger as gaps vary more relative to their
    mean).

    ``None`` when there are fewer than two gaps (no spread to measure) or
    the mean is non-positive (division undefined).
    """
    if len(gaps) < 2:
        return None
    n = len(gaps)
    return coefficient_of_variation_from_pooled(n, sum(gaps), sum(g * g for g in gaps))


def coefficient_of_variation_from_pooled(n: int, sum_sec: float | None, sumsq_sec2: float | None) -> float | None:
    """`coefficient_of_variation`'s pooled-sufficient-statistics form (see
    `mean_wait_from_pooled` for the pooling rationale). ``None`` when there
    are fewer than two samples, *sum_sec*/*sumsq_sec2* is ``None`` (see
    `mean_wait_from_pooled`'s docstring for when that happens), or the mean
    is non-positive.
    """
    if n < 2 or sum_sec is None or sumsq_sec2 is None or sum_sec <= 0:
        return None
    mean = sum_sec / n
    # E[H^2] - E[H]^2 -- algebraically non-negative, but float rounding on
    # near-zero variance (the evenly-spaced case) can nudge this a hair
    # below zero, which would otherwise raise on the sqrt below.
    variance = max(sumsq_sec2 / n - mean * mean, 0.0)
    return variance**0.5 / mean


def count_long_gaps(
    gaps: Sequence[float], scheduled_headway_sec: float | None, multiplier: float = LONG_GAP_MULTIPLIER
) -> int:
    """Count of *gaps* exceeding *multiplier* times *scheduled_headway_sec*.

    0 when there is no scheduled headway to compare against -- absence of a
    schedule is not evidence of zero long gaps, but there is nothing here
    to call "long" relative to. A caller surfacing a long-gap RATE (see
    `long_gap_rate`) must gate on a resolvable *scheduled_headway_sec*
    itself rather than trust a bare 0 count from this function alone.
    """
    if not scheduled_headway_sec or scheduled_headway_sec <= 0:
        return 0
    threshold = multiplier * scheduled_headway_sec
    return sum(1 for g in gaps if g > threshold)


def long_gap_rate(
    gaps: Sequence[float], scheduled_headway_sec: float | None, multiplier: float = LONG_GAP_MULTIPLIER
) -> float | None:
    """Fraction of *gaps* exceeding *multiplier* times *scheduled_headway_sec*.

    ``None`` when there are no gaps to rate, or no scheduled headway to
    compare against (an unclassified route has nothing to call "long"
    relative to).
    """
    if not gaps or not scheduled_headway_sec or scheduled_headway_sec <= 0:
        return None
    return count_long_gaps(gaps, scheduled_headway_sec, multiplier) / len(gaps)
