"""Reading an operator's run step by step, from its umbrella row and the step
rows filed under it (``api.admin_runs.build_latest_runs``).

The agencies page draws a run as a fixed sequence of steps -- 時刻表 → 取込 →
集計 for a chained update -- and every state it shows (done with a result,
the current step, waiting, stopped with an error, never run) is decided here,
so the page only renders.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from api.admin_runs import build_latest_runs, in_flight_args

_T0 = datetime(2026, 10, 13, 5, 0, tzinfo=timezone.utc)


def _row(run_id: int, kind: str, status: str, *, parent: int | None = None, minute: int = 0, **extra: Any):
    finished = None if status == "running" else _T0 + timedelta(minutes=minute + 1)
    return {
        "run_id": run_id,
        "parent_run_id": parent,
        "kind": kind,
        "agency_id": extra.pop("agency_id", 9),
        "started_at": _T0 + timedelta(minutes=minute),
        "finished_at": extra.pop("finished_at", finished),
        "status": status,
        "rows": extra.pop("rows", None),
        "error": extra.pop("error", None),
    }


def _steps(rows: list[dict[str, Any]], agency_id: int = 9) -> list[tuple[str, str]]:
    run = build_latest_runs(rows)[agency_id]
    return [(s["step"], s["status"]) for s in run["steps"]]


def test_a_chained_update_reads_as_three_steps_with_the_current_one_running():
    rows = [
        _row(1, "update", "running"),
        _row(2, "static", "ok", parent=1, rows=27),
        _row(3, "ingest", "running", parent=1, minute=1),
    ]
    run = build_latest_runs(rows)[9]
    assert run["kind"] == "update"
    assert run["status"] == "running"
    assert [(s["step"], s["status"]) for s in run["steps"]] == [
        ("static", "ok"),
        ("ingest", "running"),
        ("analyze", "pending"),
    ]
    assert run["steps"][0]["rows"] == 27
    assert run["steps"][1]["finished_at"] is None


def test_promotion_is_part_of_the_ingest_step_and_extends_its_duration():
    rows = [
        _row(1, "update", "running"),
        _row(2, "static", "ok", parent=1),
        _row(3, "ingest", "ok", parent=1, minute=1, rows=500),
        _row(4, "promote", "ok", parent=1, minute=2, rows=480),
    ]
    run = build_latest_runs(rows)[9]
    ingest = run["steps"][1]
    assert ingest["status"] == "ok"
    assert ingest["rows"] == 500
    assert ingest["started_at"] == "2026-10-13T05:01:00Z"
    assert ingest["finished_at"] == "2026-10-13T05:03:00Z"


def test_an_ingest_step_still_waiting_on_its_promotion_is_running_not_done():
    """Between the ingest row closing and the promotion row opening the step
    is still in progress; reading it as done would flash a ✓ and then take it
    back."""
    rows = [
        _row(1, "update", "running"),
        _row(2, "static", "ok", parent=1),
        _row(3, "ingest", "ok", parent=1, minute=1),
    ]
    assert _steps(rows)[1] == ("ingest", "running")


def test_a_failed_step_carries_its_error_and_the_steps_after_it_never_ran():
    error = "StaticRefreshFailed: the source has nothing newer, but this agency has no timetable loaded"
    rows = [
        _row(1, "update", "error", error="static: StaticRefreshFailed"),
        _row(2, "static", "error", parent=1, error=error),
    ]
    run = build_latest_runs(rows)[9]
    assert [(s["step"], s["status"]) for s in run["steps"]] == [
        ("static", "error"),
        ("ingest", "not_run"),
        ("analyze", "not_run"),
    ]
    assert run["steps"][0]["error"] == error


def test_a_promotion_failure_is_the_ingest_steps_error():
    rows = [
        _row(1, "update", "error"),
        _row(2, "static", "ok", parent=1),
        _row(3, "ingest", "ok", parent=1, minute=1),
        _row(4, "promote", "error", parent=1, minute=2, error="RuntimeError: boom"),
    ]
    run = build_latest_runs(rows)[9]
    assert run["steps"][1]["status"] == "error"
    assert run["steps"][1]["error"] == "RuntimeError: boom"
    assert run["steps"][2]["status"] == "not_run"


def test_a_timetable_check_that_found_nothing_newer_is_its_own_step_state():
    rows = [
        _row(1, "update", "running"),
        _row(2, "static", "unchanged", parent=1),
        _row(3, "ingest", "running", parent=1, minute=1),
    ]
    assert _steps(rows)[0] == ("static", "unchanged")


def test_a_run_the_advisory_lock_turned_away_keeps_its_skipped_status_and_unrun_steps():
    rows = [
        _row(1, "update", "skipped"),
        _row(2, "static", "ok", parent=1),
    ]
    run = build_latest_runs(rows)[9]
    assert run["status"] == "skipped"
    assert _steps(rows) == [("static", "ok"), ("ingest", "not_run"), ("analyze", "not_run")]


def test_a_reanalyze_reads_as_its_own_two_steps():
    rows = [
        _row(1, "ingest", "ok"),
        _row(2, "ingest", "ok", parent=1),
        _row(3, "promote", "ok", parent=1, minute=1),
        _row(4, "analyze", "ok", parent=1, minute=2),
    ]
    assert _steps(rows) == [("ingest", "ok"), ("analyze", "ok")]


def test_a_timetable_reload_alone_is_one_step():
    rows = [_row(1, "static", "running"), _row(2, "static", "running", parent=1)]
    assert _steps(rows) == [("static", "running")]


def test_a_run_with_no_step_rows_reads_its_own_row_as_its_only_evidence():
    """A run whose steps were never filed under it -- one recorded before
    steps were linked, or one turned away before writing any -- still says
    what its own row knows rather than claiming nothing ran."""
    assert _steps([_row(1, "analyze", "ok")]) == [("analyze", "ok")]
    assert _steps([_row(1, "ingest", "skipped")]) == [("ingest", "skipped"), ("analyze", "not_run")]


def test_only_each_agencys_newest_umbrella_is_read():
    rows = [
        _row(1, "update", "ok", agency_id=9),
        _row(2, "static", "ok", parent=1, agency_id=9),
        _row(5, "static", "running", agency_id=9, minute=30),
        _row(6, "static", "running", parent=5, agency_id=9, minute=30),
        _row(7, "ingest", "ok", agency_id=4),
    ]
    runs = build_latest_runs(rows)
    assert runs[9]["run_id"] == 5
    assert runs[9]["kind"] == "static"
    assert runs[4]["run_id"] == 7


def test_timestamps_are_utc_iso_strings():
    run = build_latest_runs([_row(1, "update", "running")])[9]
    assert run["started_at"] == "2026-10-13T05:00:00Z"
    assert run["finished_at"] is None


def test_in_flight_reads_rows_the_way_the_reaper_spares_them():
    """Younger than the reap age, or of a locked kind while the ingest/analyze
    lock is held: the same two conditions `reap_abandoned_runs` leaves a row
    alone under, so a press is refused exactly while the reaper would not
    yet close the row."""
    from api.admin_runs import AGENCY_RUN_IN_FLIGHT_SQL
    from pipeline.locks import INGEST_ANALYZE_LOCK_KEY
    from pipeline.runs import DEFAULT_REAP_AGE, LOCKED_RUN_KINDS

    now = datetime(2026, 10, 13, 12, 0, tzinfo=timezone.utc)
    assert in_flight_args(now) == (
        now - DEFAULT_REAP_AGE,
        list(LOCKED_RUN_KINDS),
        INGEST_ANALYZE_LOCK_KEY >> 32,
        INGEST_ANALYZE_LOCK_KEY & 0xFFFFFFFF,
    )
    assert "FROM pg_locks" in AGENCY_RUN_IN_FLIGHT_SQL
    assert "kind = ANY($2::text[])" in AGENCY_RUN_IN_FLIGHT_SQL
