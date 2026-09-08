"""Headway reconstruction and high-frequency route classification.

A route's "headway" is the time gap between two consecutive vehicles serving
the same stop on the same route. This module has two independent halves:

- Reconstructing ACTUAL headways from GTFS-RT observations, keyed by the
  physical stop (`updates.stop_id`, not `stop_sequence` -- a looping or
  branching route can visit the same physical stop at more than one
  `stop_sequence`, and grouping by `stop_sequence` instead would treat those
  as different stops and miss the true gap between vehicles that actually
  call at the same platform). Only available for an agency whose ingest
  strategy is confirmed to populate `stop_id` (today: `static_join`;
  `aomori_regex` always leaves it NULL -- see
  `pipeline.strategies.static_join`'s module docstring).
- Deriving the SCHEDULED headway median from the static GTFS `stop_times`
  table (`static_stop_times` in Postgres), independent of any RT data, used
  to classify a route as "high-frequency".

Neither half depends on the other: the scheduled classification can be
computed the moment a static feed is loaded, while actual-headway
reconstruction needs live RT history to have accumulated.
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
