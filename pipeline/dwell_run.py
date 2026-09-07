"""Per-stop-visit dwell-time and running-time decomposition of `arr_delay`
(populated only for `ingest_strategy == 'static_join'` feeds) and `dep_delay`.

Dwell time is time spent AT a stop: this visit's actual departure minus its
own actual arrival. Running time is time spent BETWEEN stops: this visit's
actual arrival minus the PREVIOUS stop visit's actual departure (same trip,
same service day). Both derive an "actual" timestamp (seconds since the
service day's midnight) from the static schedule's own arrival_time/
departure_time (``static_stop_times``) plus the RT feed's `arr_delay`/
`dep_delay` for that stop visit -- `agg_route_daily_dist` and every other
pre-existing `agg_*` builder never read `arr_delay` or the schedule's
`arrival_time`; `_analyze_deduped` carries `arr_delay` solely so this
module's own analyze()-time materialization (`agg_route_daily_dwell_run`,
see pipeline/analyze.py) can consume it.

A dwell-time observation needs this visit's own `arr_delay`; a running-time
observation needs this visit's `arr_delay` too, PLUS the PREVIOUS visit's
`dep_delay` (always present once dep_delay is non-NULL at all -- see
pipeline.db.build_dedup_ch_sql's ``dep_delay IS NOT NULL`` filter). So a
stop visit with `arr_delay` populated contributes a dwell-time observation
always, and a running-time observation whenever a previous visit exists in
the same trip/service day -- `arr_delay`'s own sparse, per-row coverage (see
pipeline/strategies/static_join.py's parse_feed docstring) means most stop
visits contribute neither.

Only ingest strategies confirmed to send `StopTimeUpdate.arrival`
(`ingest_strategy == 'static_join'` -- same agencies as
`pipeline.reports.service_delivered`) can ever produce a non-NULL
`arr_delay`; `aomori_regex` always leaves it NULL (see
pipeline/strategies/aomori_regex.py). `pipeline.analyze.analyze()`'s
`agg_route_daily_dwell_run` builder is gated on that same flag, the same
"not available" pattern `agg_service_delivered_daily` already uses.
"""

from __future__ import annotations

from pipeline.histogram import bucketize, n_buckets_for, percentile_from_hist

# Bucket bounds tuned to dwell time's own scale: most stops have a dwell of
# a few seconds to roughly a minute, with occasional multi-minute scheduled
# holds at timepoints -- distinct from the delay histogram's [-300, 1800)
# range, which is sized for departure delay, not time spent at a stop. A
# negative dwell reading is possible (arr_delay/dep_delay are independently
# refining RT estimates, not a single atomic snapshot, so a later-polled
# dep_delay can occasionally imply an earlier actual departure than the
# arrival estimate) and lands in the underflow bucket rather than being
# clamped or dropped.
DWELL_LO = -120
DWELL_HI = 600
DWELL_WIDTH = 30
DWELL_N_BUCKETS = n_buckets_for(lo=DWELL_LO, hi=DWELL_HI, width=DWELL_WIDTH)

# Bucket bounds tuned to inter-stop running time: typically tens of seconds
# to several minutes depending on stop spacing, with a half-hour ceiling
# before falling into overflow (long-haul express hops). A running time at
# or below zero shouldn't happen in clean data (stops are ordered by
# stop_sequence) but is possible from data anomalies and lands in the
# underflow bucket like any other out-of-range reading, rather than being
# silently dropped.
RUN_LO = 0
RUN_HI = 1800
RUN_WIDTH = 60
RUN_N_BUCKETS = n_buckets_for(lo=RUN_LO, hi=RUN_HI, width=RUN_WIDTH)


def bucketize_dwell(dwell_sec: int) -> int:
    """Bucket index for a dwell-time observation. See :data:`DWELL_LO`/
    :data:`DWELL_HI`/:data:`DWELL_WIDTH` for the bounds."""
    return bucketize(dwell_sec, lo=DWELL_LO, hi=DWELL_HI, width=DWELL_WIDTH)


def bucketize_running(running_sec: int) -> int:
    """Bucket index for a running-time observation. See :data:`RUN_LO`/
    :data:`RUN_HI`/:data:`RUN_WIDTH` for the bounds."""
    return bucketize(running_sec, lo=RUN_LO, hi=RUN_HI, width=RUN_WIDTH)


def percentile_from_dwell_hist(counts: list[int], q: float) -> float | None:
    """Interpolate a quantile (0..1) from a merged dwell-time histogram (see
    :func:`pipeline.histogram.percentile_from_hist`)."""
    return percentile_from_hist(counts, q, lo=DWELL_LO, hi=DWELL_HI, width=DWELL_WIDTH)


def percentile_from_run_hist(counts: list[int], q: float) -> float | None:
    """Interpolate a quantile (0..1) from a merged running-time histogram (see
    :func:`pipeline.histogram.percentile_from_hist`)."""
    return percentile_from_hist(counts, q, lo=RUN_LO, hi=RUN_HI, width=RUN_WIDTH)


def actual_arrival_sec(sched_arr_sec: int | None, arr_delay: int | None) -> int | None:
    """Actual arrival time (seconds since the service day's midnight), or
    ``None`` when the RT feed never sent this stop visit's arrival
    (`arr_delay` is NULL) or the static schedule has no `arrival_time` for
    this stop visit."""
    if sched_arr_sec is None or arr_delay is None:
        return None
    return sched_arr_sec + arr_delay


def actual_departure_sec(sched_dep_sec: int | None, dep_delay: int) -> int | None:
    """Actual departure time (seconds since the service day's midnight), or
    ``None`` when the static schedule has no `departure_time` for this stop
    visit."""
    if sched_dep_sec is None:
        return None
    return sched_dep_sec + dep_delay


def dwell_seconds(actual_arr_sec: int | None, actual_dep_sec: int | None) -> int | None:
    """Time spent at this stop: this visit's departure minus its own
    arrival. ``None`` when either side is unknown (see
    :func:`actual_arrival_sec`/:func:`actual_departure_sec`)."""
    if actual_arr_sec is None or actual_dep_sec is None:
        return None
    return actual_dep_sec - actual_arr_sec


def running_seconds(prev_actual_dep_sec: int | None, actual_arr_sec: int | None) -> int | None:
    """Time spent travelling to this stop: this visit's arrival minus the
    PREVIOUS stop visit's departure (same trip, same service day). ``None``
    when either side is unknown -- notably, a trip's very first stop always
    has no previous departure to run from."""
    if prev_actual_dep_sec is None or actual_arr_sec is None:
        return None
    return actual_arr_sec - prev_actual_dep_sec


class StopVisit:
    """One stop visit's schedule + RT delay inputs, to be ordered by
    `stop_sequence` within a single trip's single service day before being
    passed to :func:`compute_trip_dwell_running`.

    `sched_arr_sec`/`sched_dep_sec` are the static schedule's arrival_time/
    departure_time for this stop visit, in seconds since the service day's
    midnight (``None`` when the schedule has no value for this stop --
    common for `arrival_time` at non-timepoint stops in some GTFS feeds).
    `arr_delay` is ``None`` when the RT feed never sent this stop visit's
    arrival; `dep_delay` is always a real int (see module docstring).
    """

    __slots__ = ("arr_delay", "dep_delay", "sched_arr_sec", "sched_dep_sec", "stop_sequence")

    def __init__(
        self,
        stop_sequence: int,
        sched_arr_sec: int | None,
        sched_dep_sec: int | None,
        arr_delay: int | None,
        dep_delay: int,
    ) -> None:
        self.stop_sequence = stop_sequence
        self.sched_arr_sec = sched_arr_sec
        self.sched_dep_sec = sched_dep_sec
        self.arr_delay = arr_delay
        self.dep_delay = dep_delay


def compute_trip_dwell_running(visits: list[StopVisit]) -> list[dict]:
    """Return one dict per visit, sorted by `stop_sequence`:
    ``{"stop_sequence": int, "dwell_sec": int | None, "running_sec": int | None}``.

    `dwell_sec` needs only this visit's own data; `running_sec` additionally
    needs the PREVIOUS visit's actual departure, so a trip's very first stop
    always has ``running_sec is None`` (there is nothing to run FROM) even
    when its own arrival is fully known.
    """
    ordered = sorted(visits, key=lambda v: v.stop_sequence)
    out = []
    prev_actual_dep: int | None = None
    for v in ordered:
        actual_arr = actual_arrival_sec(v.sched_arr_sec, v.arr_delay)
        actual_dep = actual_departure_sec(v.sched_dep_sec, v.dep_delay)
        out.append(
            {
                "stop_sequence": v.stop_sequence,
                "dwell_sec": dwell_seconds(actual_arr, actual_dep),
                "running_sec": running_seconds(prev_actual_dep, actual_arr),
            }
        )
        prev_actual_dep = actual_dep
    return out
