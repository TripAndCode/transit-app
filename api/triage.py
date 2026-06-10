"""Pure baseline-relative triage classification for the 最新観測 tab.

No DB, no I/O — just the decision rule, so it is fast to unit-test and the
exact thresholds live in one place. See
docs/superpowers/specs/2026-06-10-latest-obs-triage-design.md.
"""

from typing import Literal, Optional, Tuple

Bucket = Literal["anomaly", "watch", "normal", "no_baseline"]

# A route with fewer than this many observations today is too thin to trust as
# an anomaly — a single outlier reading could dominate. Such routes are never
# promoted into `anomaly`; they cap at `watch` and carry low_confidence=True.
LOW_CONFIDENCE_SAMPLES = 30


def classify_route(
    avg_delay_sec: Optional[int],
    baseline_avg_sec: Optional[float],
    baseline_p90_sec: Optional[float],
    samples: int,
) -> Tuple[Bucket, Optional[int], bool]:
    """Classify one route for today.

    Returns ``(bucket, deviation_sec, low_confidence)`` where:
      - ``deviation_sec`` = today's avg minus baseline avg (rounded int), or
        ``None`` when there is no baseline.
      - Buckets (only when a baseline exists):
          anomaly  today avg > baseline p90
          watch    today avg > midpoint(baseline avg, baseline p90)
          normal   otherwise
      - ``low_confidence`` (samples < LOW_CONFIDENCE_SAMPLES) caps the bucket at
        ``watch`` — a thin route never lands in ``anomaly``.
    """
    low_confidence = samples < LOW_CONFIDENCE_SAMPLES
    if avg_delay_sec is None or baseline_avg_sec is None or baseline_p90_sec is None:
        return "no_baseline", None, low_confidence

    deviation_sec = round(avg_delay_sec - baseline_avg_sec)
    midpoint = baseline_avg_sec + 0.5 * (baseline_p90_sec - baseline_avg_sec)

    if avg_delay_sec > baseline_p90_sec:
        bucket: Bucket = "anomaly"
    elif avg_delay_sec > midpoint:
        bucket = "watch"
    else:
        bucket = "normal"

    if low_confidence and bucket == "anomaly":
        bucket = "watch"

    return bucket, deviation_sec, low_confidence
