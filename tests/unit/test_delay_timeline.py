"""Day-playback timeline: bucketing, folding and the rendered ClickHouse SQL.

Pure-logic only — no ClickHouse and no Postgres. The SQL is asserted as text
(the shared dedup CTE must be the source, the bucket must be derived from
`scheduled_sec`, and the window must be parameterised) and the aggregation is
asserted against mocked rows.
"""

from datetime import date, datetime

import pytest

from api.range import RangeCtx
from pipeline.reports.timeline import (
    ALLOWED_STEP_MINUTES,
    MAX_POINTS_PER_FRAME,
    MIN_BUCKET_SAMPLES,
    PLAYBACK_END_SEC,
    PLAYBACK_START_SEC,
    bucket_count,
    bucket_label,
    bucket_of,
    build_frames,
    build_timeline_ch_sql,
    fold_stop_buckets,
    playback_day_for,
)


def _ctx(day: date = date(2026, 3, 4)) -> RangeCtx:
    return RangeCtx(from_date=day, to_date=day)


# ── bucketing ────────────────────────────────────────────────────────────────


def test_allowed_steps_are_exactly_15_and_60():
    assert ALLOWED_STEP_MINUTES == (15, 60)


@pytest.mark.parametrize(("step", "expected"), [(60, 19), (15, 76)])
def test_bucket_count_covers_05_00_to_24_00(step, expected):
    assert bucket_count(step) == expected
    assert (PLAYBACK_END_SEC - PLAYBACK_START_SEC) // (step * 60) == expected


@pytest.mark.parametrize(
    ("index", "step", "label"),
    [
        (0, 60, "05:00"),
        (1, 60, "06:00"),
        (18, 60, "23:00"),
        (0, 15, "05:00"),
        (1, 15, "05:15"),
        (75, 15, "23:45"),
    ],
)
def test_bucket_label_is_a_zero_padded_clock_time(index, step, label):
    assert bucket_label(index, step) == label


@pytest.mark.parametrize(
    ("scheduled_sec", "step", "expected"),
    [
        (5 * 3600, 60, 0),
        (5 * 3600 + 59 * 60, 60, 0),
        (6 * 3600, 60, 1),
        (23 * 3600 + 3599, 60, 18),
        (5 * 3600 + 15 * 60, 15, 1),
        # Before the rail's window (pre-05:00) and GTFS extended hours
        # (>= 24:00, an after-midnight continuation) both sit outside it.
        (4 * 3600 + 3599, 60, None),
        (0, 60, None),
        (PLAYBACK_END_SEC, 60, None),
        (25 * 3600, 60, None),
        (None, 60, None),
    ],
)
def test_bucket_of_maps_a_schedule_second_into_the_window(scheduled_sec, step, expected):
    assert bucket_of(scheduled_sec, step) == expected


# ── rendered SQL ─────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("captured", "expected"),
    [
        # Inside the window: the stamped date is the service day.
        (datetime(2026, 3, 4, 8, 30), date(2026, 3, 4)),
        (datetime(2026, 3, 4, 23, 59, 59), date(2026, 3, 4)),
        # Exactly when the window opens -- the new day owns it.
        (datetime(2026, 3, 4, 5, 0, 0), date(2026, 3, 4)),
        # After midnight but before it opens: a late run still belongs to
        # the day that is finishing, whose rail actually has data.
        (datetime(2026, 3, 4, 0, 5), date(2026, 3, 3)),
        (datetime(2026, 3, 4, 4, 59, 59), date(2026, 3, 3)),
        # Across a month boundary, so the rollback is a real date subtraction.
        (datetime(2026, 3, 1, 0, 20), date(2026, 2, 28)),
    ],
)
def test_playback_day_rolls_back_before_the_window_opens(captured, expected):
    assert playback_day_for(captured) == expected


def test_timeline_sql_orders_before_truncating_to_the_row_guard():
    """An unordered LIMIT would keep an arbitrary slice that can differ
    between two recomputes of the very same day."""
    sql, _ = build_timeline_ch_sql(_ctx(), 60)
    assert "ORDER BY samples DESC" in sql
    assert sql.index("ORDER BY samples DESC") < sql.index("LIMIT {tl_max_rows:UInt32}")


def test_timeline_sql_reads_the_shared_dedup_cte_and_buckets_by_scheduled_sec():
    sql, params = build_timeline_ch_sql(_ctx(), 60)

    # Built on the shared dedup CTE, not a hand-rolled scan of `updates`.
    assert sql.startswith("WITH deduped AS (")
    assert "argMax(u.dep_delay, (u.captured_at, u.file_name))" in sql
    assert "any(u.scheduled_sec) AS scheduled_sec" in sql
    assert "FROM deduped" in sql

    # The bucket is derived from scheduled_sec, not captured_at: the rail is a
    # timetable clock, not a feed-poll clock.
    assert "intDiv(scheduled_sec - {tl_start_sec:UInt32}, {tl_step_sec:UInt32}) AS bucket" in sql
    assert "GROUP BY bucket, trip_id, stop_sequence" in sql
    assert "captured_at) AS bucket" not in sql

    # Window bounds and the row guard are parameters, never interpolated values.
    assert "scheduled_sec >= {tl_start_sec:UInt32}" in sql
    assert "scheduled_sec < {tl_end_sec:UInt32}" in sql
    assert "LIMIT {tl_max_rows:UInt32}" in sql
    assert params["tl_start_sec"] == PLAYBACK_START_SEC
    assert params["tl_end_sec"] == PLAYBACK_END_SEC
    assert params["tl_step_sec"] == 3600

    # The single service day travels as the dedup CTE's own date parameters.
    assert params["ch_from_date"] == date(2026, 3, 4)
    assert params["ch_to_date"] == date(2026, 3, 4)
    # agency_id stays the caller's job, as with every other ClickHouse site.
    assert "agency_id" not in params
    assert "{agency_id:UInt16}" in sql


def test_timeline_sql_step_parameter_tracks_the_requested_step():
    _, params = build_timeline_ch_sql(_ctx(), 15)
    assert params["tl_step_sec"] == 900


def test_timeline_sql_rejects_an_unsupported_step():
    with pytest.raises(ValueError):
        build_timeline_ch_sql(_ctx(), 30)


# ── folding mocked rows ──────────────────────────────────────────────────────

_PAIR_TO_STOP = {
    ("T1", 1): "S_A",
    ("T1", 2): "S_B",
    ("T2", 1): "S_A",
}


def test_fold_sums_every_trip_visit_that_maps_to_the_same_stop_and_bucket():
    rows = [
        {"bucket": 3, "trip_id": "T1", "stop_sequence": 1, "delay_sum": 120, "samples": 2},
        {"bucket": 3, "trip_id": "T2", "stop_sequence": 1, "delay_sum": 60, "samples": 1},
        {"bucket": 4, "trip_id": "T1", "stop_sequence": 2, "delay_sum": 30, "samples": 1},
        # No static mapping for this visit — it has no coordinates, so it is dropped.
        {"bucket": 3, "trip_id": "T9", "stop_sequence": 7, "delay_sum": 999, "samples": 9},
    ]
    assert fold_stop_buckets(rows, _PAIR_TO_STOP) == {
        (3, "S_A"): [180, 3],
        (4, "S_B"): [30, 1],
    }


def test_fold_ignores_a_bucket_index_outside_the_window():
    rows = [{"bucket": -1, "trip_id": "T1", "stop_sequence": 1, "delay_sum": 60, "samples": 5}]
    assert fold_stop_buckets(rows, _PAIR_TO_STOP) == {}


# ── frames ───────────────────────────────────────────────────────────────────

_GEO = {
    "S_A": {"stop_name": "A", "lon": 140.7, "lat": 40.8},
    "S_B": {"stop_name": "B", "lon": 140.8, "lat": 40.9},
}


def test_frames_are_dense_and_labelled_even_where_no_stop_qualifies():
    frames = build_frames({(0, "S_A"): [180, 3]}, _GEO, 60)
    assert len(frames) == bucket_count(60)
    assert [f["t"] for f in frames][:3] == ["05:00", "06:00", "07:00"]
    assert frames[0]["points"] and frames[1]["points"] == []
    assert frames[1]["mean_delay_min"] is None
    assert frames[1]["samples"] == 0


def test_a_stop_below_the_sample_floor_is_dropped_from_its_bucket():
    folded = {
        (0, "S_A"): [60, MIN_BUCKET_SAMPLES - 1],
        (0, "S_B"): [600, MIN_BUCKET_SAMPLES],
    }
    frames = build_frames(folded, _GEO, 60)
    assert [p["stop_id"] for p in frames[0]["points"]] == ["S_B"]
    # The dropped stop must not leak into the frame's headline either.
    assert frames[0]["samples"] == MIN_BUCKET_SAMPLES


def test_a_point_carries_its_position_and_a_two_dp_mean_in_minutes():
    frames = build_frames({(0, "S_A"): [185, 3]}, _GEO, 60)
    assert frames[0]["points"][0] == {
        "stop_id": "S_A",
        "stop_name": "A",
        "lon": 140.7,
        "lat": 40.8,
        "avg_delay_min": 1.03,
        "samples": 3,
    }
    assert frames[0]["mean_delay_min"] == 1.03


def test_a_stop_without_coordinates_is_dropped():
    frames = build_frames({(0, "S_ghost"): [600, 10]}, _GEO, 60)
    assert frames[0]["points"] == []


def test_a_crowded_bucket_is_capped_to_the_best_evidenced_stops():
    geo = {f"S{i:04d}": {"stop_name": f"n{i}", "lon": 140.0, "lat": 40.0} for i in range(MAX_POINTS_PER_FRAME + 5)}
    # Give the last five stops the most samples so the cap is provably by
    # evidence, not by insertion or name order.
    folded = {}
    for i in range(MAX_POINTS_PER_FRAME + 5):
        samples = 1000 if i >= MAX_POINTS_PER_FRAME else 3
        folded[(0, f"S{i:04d}")] = [60 * samples, samples]
    frames = build_frames(folded, geo, 60)
    kept = [p["stop_id"] for p in frames[0]["points"]]
    assert len(kept) == MAX_POINTS_PER_FRAME
    assert set(f"S{i:04d}" for i in range(MAX_POINTS_PER_FRAME, MAX_POINTS_PER_FRAME + 5)) <= set(kept)
    # Output order is stable regardless of which stops survived the cap.
    assert kept == sorted(kept)


def test_frame_mean_is_sample_weighted_not_a_mean_of_means():
    folded = {(0, "S_A"): [60 * 100, 100], (0, "S_B"): [0, 4]}
    frames = build_frames(folded, _GEO, 60)
    # 6000 sec over 104 samples = 0.96 min, not (1.0 + 0.0) / 2.
    assert frames[0]["mean_delay_min"] == 0.96
