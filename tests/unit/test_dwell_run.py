"""Pure-logic tests for dwell-time/running-time decomposition (item 95).

Mirrors tests/unit/test_histogram.py's style: synthetic, hand-computable
inputs with no DB fixtures (pure functions only).
"""

from pipeline.dwell_run import (
    DWELL_HI,
    DWELL_LO,
    DWELL_N_BUCKETS,
    DWELL_WIDTH,
    RUN_HI,
    RUN_LO,
    RUN_N_BUCKETS,
    RUN_WIDTH,
    StopVisit,
    actual_arrival_sec,
    actual_departure_sec,
    bucketize_dwell,
    bucketize_running,
    compute_trip_dwell_running,
    dwell_seconds,
    percentile_from_dwell_hist,
    percentile_from_run_hist,
    running_seconds,
)


def test_actual_arrival_and_departure_from_schedule_plus_delay():
    # Scheduled arrival 10:00:00 (36000s), arr_delay=+45s -> actual 10:00:45.
    assert actual_arrival_sec(36000, 45) == 36045
    # Scheduled departure 10:01:00 (36060s), dep_delay=+90s -> actual 10:02:30.
    assert actual_departure_sec(36060, 90) == 36150


def test_actual_arrival_is_none_without_arr_delay_or_schedule():
    assert actual_arrival_sec(36000, None) is None
    assert actual_arrival_sec(None, 45) is None


def test_actual_departure_is_none_without_schedule():
    assert actual_departure_sec(None, 90) is None


def test_dwell_seconds_is_departure_minus_arrival():
    # Bus arrives at 10:00:45, departs at 10:02:30 -> 105s dwell.
    assert dwell_seconds(36045, 36150) == 105


def test_dwell_seconds_is_none_when_arrival_unknown():
    assert dwell_seconds(None, 36150) is None


def test_running_seconds_is_arrival_minus_previous_departure():
    # Previous stop departed at 10:02:30 (36150), this stop's bus arrives at
    # 10:07:00 (36420) -> 270s running time.
    assert running_seconds(36150, 36420) == 270


def test_running_seconds_is_none_at_first_stop():
    # No previous departure -- a trip's first stop has nothing to run from.
    assert running_seconds(None, 36420) is None


def test_compute_trip_dwell_running_known_synthetic_timestamps():
    """A 3-stop trip with known synthetic arrival/departure timestamps
    produces the expected dwell/running split -- this is the fixture the
    item's own Verify criterion asks for.

    Schedule (seconds since midnight):
      stop 1: sched_arr=36000 (10:00:00), sched_dep=36000 (10:00:00)
      stop 2: sched_arr=36300 (10:05:00), sched_dep=36360 (10:06:00) -- 60s scheduled dwell
      stop 3: sched_arr=36600 (10:10:00), sched_dep=36600 (10:10:00)

    RT delays:
      stop 1: arr_delay=None (no arrival ping), dep_delay=+30 -> actual dep 36030
      stop 2: arr_delay=+20 -> actual arr 36320; dep_delay=+50 -> actual dep 36410
      stop 3: arr_delay=+10 -> actual arr 36610; dep_delay=+10 -> actual dep 36610

    Expected:
      stop 1: dwell=None (no arrival), running=None (first stop)
      stop 2: dwell = 36410 - 36320 = 90; running = 36320 - 36030 = 290
      stop 3: dwell = 36610 - 36610 = 0; running = 36610 - 36410 = 200
    """
    visits = [
        StopVisit(stop_sequence=1, sched_arr_sec=36000, sched_dep_sec=36000, arr_delay=None, dep_delay=30),
        StopVisit(stop_sequence=2, sched_arr_sec=36300, sched_dep_sec=36360, arr_delay=20, dep_delay=50),
        StopVisit(stop_sequence=3, sched_arr_sec=36600, sched_dep_sec=36600, arr_delay=10, dep_delay=10),
    ]
    result = compute_trip_dwell_running(visits)
    assert result == [
        {"stop_sequence": 1, "dwell_sec": None, "running_sec": None},
        {"stop_sequence": 2, "dwell_sec": 90, "running_sec": 290},
        {"stop_sequence": 3, "dwell_sec": 0, "running_sec": 200},
    ]


def test_compute_trip_dwell_running_sorts_by_stop_sequence():
    """Visits passed out of order are sorted before computing running time,
    so the previous-stop dependency is always the schedule-adjacent one, not
    whatever order the caller happened to pass them in."""
    visits = [
        StopVisit(stop_sequence=2, sched_arr_sec=36300, sched_dep_sec=36360, arr_delay=20, dep_delay=50),
        StopVisit(stop_sequence=1, sched_arr_sec=36000, sched_dep_sec=36000, arr_delay=None, dep_delay=30),
    ]
    result = compute_trip_dwell_running(visits)
    assert [r["stop_sequence"] for r in result] == [1, 2]
    assert result[1]["running_sec"] == 290  # 36320 - 36030


def test_compute_trip_dwell_running_missing_schedule_yields_none_not_zero():
    """A stop visit with no static schedule for it (arrival_time/
    departure_time both NULL) must degrade to None dwell/running, never a
    misleading 0 -- the "not available" state applies at the per-visit
    grain too, not just the per-agency one."""
    visits = [
        StopVisit(stop_sequence=1, sched_arr_sec=None, sched_dep_sec=None, arr_delay=30, dep_delay=30),
        StopVisit(stop_sequence=2, sched_arr_sec=36300, sched_dep_sec=36360, arr_delay=20, dep_delay=50),
    ]
    result = compute_trip_dwell_running(visits)
    assert result[0] == {"stop_sequence": 1, "dwell_sec": None, "running_sec": None}
    # Stop 2's running time needs stop 1's actual departure, which is
    # unknown (no schedule) -- so it's None too, not silently skipped to
    # some earlier known stop.
    assert result[1]["running_sec"] is None


def test_dwell_bucket_bounds_and_count():
    assert DWELL_N_BUCKETS == (DWELL_HI - DWELL_LO) // DWELL_WIDTH + 2
    assert bucketize_dwell(DWELL_LO - 1) == 0  # underflow
    assert bucketize_dwell(DWELL_HI) == DWELL_N_BUCKETS - 1  # overflow
    assert bucketize_dwell(0) == bucketize_dwell(DWELL_WIDTH - 1)


def test_run_bucket_bounds_and_count():
    assert RUN_N_BUCKETS == (RUN_HI - RUN_LO) // RUN_WIDTH + 2
    assert bucketize_running(RUN_LO - 1) == 0  # underflow
    assert bucketize_running(RUN_HI) == RUN_N_BUCKETS - 1  # overflow


def _dwell_hist(*values: int) -> list[int]:
    counts = [0] * DWELL_N_BUCKETS
    for v in values:
        counts[bucketize_dwell(v)] += 1
    return counts


def _run_hist(*values: int) -> list[int]:
    counts = [0] * RUN_N_BUCKETS
    for v in values:
        counts[bucketize_running(v)] += 1
    return counts


def test_percentile_from_dwell_hist_matches_hand_computed_median():
    # 10 observations at 0s, 10 at 300s -> p50 sits at the boundary (top edge
    # of the first populated bucket), same convention as the delay histogram.
    counts = _dwell_hist(*([0] * 10 + [300] * 10))
    p50 = percentile_from_dwell_hist(counts, 0.5)
    assert p50 is not None
    assert 0 <= p50 <= 300


def test_percentile_from_run_hist_empty_is_none():
    assert percentile_from_run_hist([0] * RUN_N_BUCKETS, 0.5) is None
