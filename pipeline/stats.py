"""Pure statistical helpers with no DB/I/O dependency.

Kept separate from ``api.triage``'s ``LOW_CONFIDENCE_SAMPLES`` (a flat
sample-count floor): a proportion's actual sampling uncertainty depends on
*both* the sample count and how close the proportion sits to 50% (variance
peaks at p=0.5 and shrinks toward 0%/100% for the same n), so two
percentages can clear the same raw sample-count floor and still carry very
different confidence. These helpers answer that narrower question for a
single binomial proportion (e.g. an on-time or late-5-minute rate).
"""

from __future__ import annotations

import math

# Standard normal critical value for a 95% two-sided interval.
_Z95 = 1.959964


def wilson_interval(successes: int, n: int, z: float = _Z95) -> tuple[float, float]:
    """Wilson score confidence interval for a binomial proportion.

    Returns ``(low, high)`` in ``[0, 1]``. This is the standard correction
    for a normal-approximation interval, which misbehaves (can exceed
    [0, 1], or collapse to zero width) for small n or a proportion near 0/1.
    ``n <= 0`` returns the maximally uninformative ``(0.0, 1.0)`` rather
    than dividing by zero.
    """
    if n <= 0:
        return (0.0, 1.0)
    if successes < 0 or successes > n:
        raise ValueError(f"successes ({successes}) must be within [0, n={n}]")
    phat = successes / n
    z2 = z * z
    denom = 1 + z2 / n
    center = phat + z2 / (2 * n)
    margin = z * math.sqrt((phat * (1 - phat) + z2 / (4 * n)) / n)
    low = (center - margin) / denom
    high = (center + margin) / denom
    return (max(0.0, low), min(1.0, high))


def pct_is_uncertain(successes: int, n: int, max_half_width_pp: float = 5.0) -> bool:
    """True when a percentage's 95% Wilson interval half-width exceeds the cutoff.

    ``max_half_width_pp`` is in percentage points (default +/-5pp). Calibrated
    against typical late-5-minute/on-time rates (single digits to ~20%,
    well away from the high-variance 50% midpoint): at that range, a
    comfortably large baseline sample (roughly n > 200) stays under a 5pp
    half-width, while a genuinely thin history does not -- so this rarely
    trips for an established route and reliably catches a new/sparse one.
    ``n <= 0`` is always uncertain (no data to estimate from).
    """
    if n <= 0:
        return True
    low, high = wilson_interval(successes, n, _Z95)
    return (high - low) * 100 / 2 > max_half_width_pp


def annotate_on_time_pct_confidence(rows: list[tuple]) -> list[tuple]:
    """Append a ``low_confidence`` bool to each on-time-style row.

    Expects the ``(route_code, service_type, pct, avg_min, samples)`` row
    shape shared by ``pipeline.reports.rankings.compute_on_time`` and its
    live fallback (``pct`` a percentage 0..100, ``samples`` the group's
    observation count). Returns a NEW list of 6-tuples; the input rows and
    ``compute_on_time``'s own contract are untouched, so callers that pool
    or re-aggregate its raw rows (e.g. ``pipeline.reports.suggest``'s
    fallback) are unaffected -- this is a display-layer annotation only.

    ``successes`` is back-derived from the already-rounded percentage
    (``round(pct/100 * samples)``) rather than re-read from an exact count
    column. That reintroduces at most rounding-sized slop into the
    confidence flag itself, which is acceptable here: the result only
    drives a coarse pass/fail caveat for display, never a stored or pooled
    statistic (unlike this codebase's exact-sum pooling elsewhere, which
    matters because it compounds across many rows).
    """
    out: list[tuple] = []
    for r in rows:
        pct, samples = float(r[2]), int(r[4])
        successes = max(0, min(samples, round(pct / 100 * samples)))
        out.append((*r, pct_is_uncertain(successes, samples)))
    return out
