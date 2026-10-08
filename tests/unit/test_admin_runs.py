"""`api.admin_runs` — the day window and row shaping behind the run timeline.

Pure functions only: the window is the same civil-day rule the rest of the
admin surface uses (JST, because that is the day an operator means), and the
shaping turns asyncpg rows into the JSON the timeline reads.
"""

from __future__ import annotations

from datetime import date, datetime, timezone

from api.admin_runs import RUNS_FOR_DAY_SQL, runs_day_bounds, shape_run


def test_the_day_window_is_a_jst_civil_day_expressed_in_utc():
    start, end = runs_day_bounds(date(2026, 9, 21))
    # JST is UTC+9 the whole year round, so the day opens at 15:00 UTC the
    # day before and is exactly 24 h long.
    assert start == datetime(2026, 9, 20, 15, 0, tzinfo=timezone.utc)
    assert end == datetime(2026, 9, 21, 15, 0, tzinfo=timezone.utc)


def test_the_window_is_half_open_so_two_days_never_claim_the_same_run():
    _, first_end = runs_day_bounds(date(2026, 9, 21))
    second_start, _ = runs_day_bounds(date(2026, 9, 22))
    assert first_end == second_start
    assert ">= $1" in RUNS_FOR_DAY_SQL and "< $2" in RUNS_FOR_DAY_SQL


def test_the_day_query_orders_oldest_first_so_the_timeline_reads_left_to_right():
    assert "ORDER BY r.started_at, r.run_id" in " ".join(RUNS_FOR_DAY_SQL.split())


def test_the_day_query_names_the_agency_without_dropping_fleet_wide_runs():
    normalized = " ".join(RUNS_FOR_DAY_SQL.split())
    assert "LEFT JOIN agencies" in normalized


def test_shape_run_renders_timestamps_as_utc_iso_strings():
    shaped = shape_run(
        {
            "run_id": 3,
            "kind": "analyze",
            "agency_id": 1,
            "agency_name": "Hokuriku",
            "started_at": datetime(2026, 9, 21, 4, 30, tzinfo=timezone.utc),
            "finished_at": None,
            "status": "running",
            "rows": None,
            "lock_wait_ms": None,
            "error": None,
            "requested_by": None,
        }
    )
    assert shaped["started_at"] == "2026-09-21T04:30:00Z"
    assert shaped["finished_at"] is None
    assert shaped["agency_name"] == "Hokuriku"


def test_the_lock_column_is_published_as_a_probe_cost_not_as_time_spent_waiting():
    """The acquire is non-blocking, so the stored milliseconds are the round
    trip that discovered the lock was held, never a queue the job sat in.
    The column keeps its historical name; the API field states what it is."""
    shaped = shape_run(
        {
            "run_id": 6,
            "kind": "ingest",
            "agency_id": None,
            "agency_name": None,
            "started_at": datetime(2026, 9, 21, 6, 0, tzinfo=timezone.utc),
            "finished_at": datetime(2026, 9, 21, 6, 0, tzinfo=timezone.utc),
            "status": "skipped",
            "rows": None,
            "lock_wait_ms": 4,
            "error": None,
            "requested_by": None,
        }
    )
    assert shaped["lock_probe_ms"] == 4
    assert "lock_wait_ms" not in shaped


def test_shape_run_keeps_a_fleet_wide_run_nameless_rather_than_inventing_one():
    shaped = shape_run(
        {
            "run_id": 4,
            "kind": "weather",
            "agency_id": None,
            "agency_name": None,
            "started_at": datetime(2026, 9, 21, 3, 0, tzinfo=timezone.utc),
            "finished_at": datetime(2026, 9, 21, 3, 1, tzinfo=timezone.utc),
            "status": "ok",
            "rows": 12,
            "lock_wait_ms": None,
            "error": None,
            "requested_by": 9,
        }
    )
    assert shaped["agency_id"] is None
    assert shaped["agency_name"] is None
    assert shaped["rows"] == 12
    assert shaped["requested_by"] == 9


def test_a_naive_timestamp_is_read_as_utc_rather_than_as_local_time():
    """asyncpg hands back aware datetimes, but a fake or a driver configured
    otherwise must not silently shift the bar by the server's offset."""
    shaped = shape_run(
        {
            "run_id": 5,
            "kind": "ingest",
            "agency_id": None,
            "agency_name": None,
            "started_at": datetime(2026, 9, 21, 6, 0),
            "finished_at": None,
            "status": "running",
            "rows": None,
            "lock_wait_ms": None,
            "error": None,
            "requested_by": None,
        }
    )
    assert shaped["started_at"] == "2026-09-21T06:00:00Z"
