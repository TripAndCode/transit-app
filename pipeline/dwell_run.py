"""Per-stop-visit dwell-time and running-time decomposition of `arr_delay`
(populated only for `ingest_strategy == 'static_join'` feeds) and `dep_delay`.

Dwell time is time spent AT a stop: this visit's actual departure minus its
own actual arrival. Running time is time spent BETWEEN stops: this visit's
actual arrival minus the PREVIOUS stop visit's actual departure (same trip,
same service day). Both derive an "actual" timestamp (seconds since the
service day's midnight) from the static schedule's arrival_time/
departure_time plus the RT feed's `arr_delay`/`dep_delay` for that stop
visit. The schedule is the archived timetable the visit's version name maps
to (`build_stop_visits_ch_sql(join_schedule=True)`). analyze computes both in
SQL, in its two builders over the same visits: `pipeline.agg_sql.dwell_run_sql`
for `agg_route_daily_dwell_run` and `pipeline.agg_sql.segment_runtime_sql` for
`agg_route_segment_runtime`. This module holds the histogram bounds the first
one buckets by.

The feed reports a stop's arrival and its departure in separate polls, never
on one row, and a trip's last stop only as an arrival.
`pipeline.db.build_stop_visits_ch_sql` pairs the two per stop visit. A visit
can therefore lack either side:

- with both, it yields a running time when the previous visit's departure
  is known, and a dwell time when its departure was observed: reported in
  a poll at or after the visit's first arrival poll. A departure whose
  every poll came before the arrival is the prediction still standing when
  the bus arrived, which says nothing about the time spent at the stop, so
  it makes no dwell sample. It still anchors the next visit's running time;
- with only an arrival (always the terminus), it yields a running time and
  no dwell, and leaves the next visit's running time unknown;
- with only a departure, it yields neither, but anchors the next visit's
  running time.

Only ingest strategies confirmed to send `StopTimeUpdate.arrival`
(`ingest_strategy == 'static_join'` -- same agencies as
`pipeline.reports.service_delivered`) can ever produce a non-NULL
`arr_delay`; `aomori_regex` always leaves it NULL (see
pipeline/strategies/aomori_regex.py). analyze's two builders are gated on
that same flag, the same "not available" pattern `agg_service_delivered_daily`
already uses.
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
# arrival estimate) and is kept rather than clamped or dropped: it fills the
# inner bins down to DWELL_LO, and only a reading below that lands in the
# underflow bucket.
DWELL_LO = -120
DWELL_HI = 600
DWELL_WIDTH = 30
DWELL_N_BUCKETS = n_buckets_for(lo=DWELL_LO, hi=DWELL_HI, width=DWELL_WIDTH)

# Bucket bounds tuned to inter-stop running time: typically tens of seconds
# to several minutes depending on stop spacing, with a half-hour ceiling
# before falling into overflow (long-haul express hops). A running time at
# or below zero shouldn't happen in clean data (stops are ordered by
# stop_sequence) but is possible from data anomalies; a negative one lands in
# the underflow bucket like any other out-of-range reading (zero is the first
# inner bin), rather than being silently dropped.
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
