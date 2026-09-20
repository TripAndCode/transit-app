"""Every gtfs_pipeline.py job leaves a pipeline_runs row, including the ones
that never ran.

The displaced case is the point: `_lock_or_skip_agency` and `_lock_or_exit`
return without doing any work, so without a `skipped` row a fleet that is
losing every other scheduled run is indistinguishable from a healthy one.
Recording that row must not disturb either command's exit-code contract --
EX_TEMPFAIL for the shell-looped single-agency commands, 1 for the
whole-fleet ones (tests/unit/test_gtfs_pipeline_lock.py pins those).

DB-free: the psycopg2 connection is mocked and `pipeline.runs` is observed
through its own helpers.
"""

from argparse import Namespace
from contextlib import ExitStack
from unittest.mock import MagicMock, patch

import pytest

import gtfs_pipeline


def _conn():
    conn = MagicMock()
    conn.cursor.return_value.__enter__.return_value.fetchall.return_value = [(1, "Agency")]
    conn.cursor.return_value.__enter__.return_value.fetchone.return_value = (11,)
    return conn


@pytest.mark.parametrize(
    "cmd,args,kind,target",
    [
        (gtfs_pipeline.cmd_ingest, Namespace(agency_id=1, folder="/tmp/x"), "ingest", "pipeline.ingest.ingest"),
        (gtfs_pipeline.cmd_analyze, Namespace(agency_id=1), "analyze", "pipeline.analyze.analyze"),
    ],
)
def test_a_displaced_single_agency_job_is_recorded_as_skipped(cmd, args, kind, target):
    conn = _conn()
    with (
        patch.object(gtfs_pipeline, "_get_conn", return_value=conn),
        patch("pipeline.locks.try_lock_ingest_analyze", return_value=False),
        patch("pipeline.clickhouse.get_client", return_value=MagicMock()),
        patch("pipeline.runs.start_run") as start,
        patch(target),
    ):
        with pytest.raises(SystemExit):
            cmd(args)

    assert start.call_count == 1
    assert start.call_args.args[1] == kind
    assert start.call_args.kwargs["status"] == "skipped"
    assert start.call_args.kwargs["lock_wait_ms"] is not None


def test_the_skip_row_does_not_change_the_shell_loop_exit_code():
    conn = _conn()
    with (
        patch.object(gtfs_pipeline, "_get_conn", return_value=conn),
        patch("pipeline.locks.try_lock_ingest_analyze", return_value=False),
        patch("pipeline.clickhouse.get_client", return_value=MagicMock()),
        patch("pipeline.ingest.ingest"),
    ):
        with pytest.raises(SystemExit) as exc_info:
            gtfs_pipeline.cmd_ingest(Namespace(agency_id=1, folder="/tmp/x"))
    assert exc_info.value.code == gtfs_pipeline.EX_TEMPFAIL


def test_a_displaced_whole_fleet_job_is_recorded_and_still_exits_1():
    conn = _conn()
    with (
        patch.object(gtfs_pipeline, "_get_conn", return_value=conn),
        patch("pipeline.locks.try_lock_ingest_analyze", return_value=False),
        patch("pipeline.clickhouse.get_client", return_value=MagicMock()),
        patch("pipeline.runs.start_run") as start,
        patch("pipeline.analyze.analyze"),
    ):
        with pytest.raises(SystemExit) as exc_info:
            gtfs_pipeline.cmd_analyze_all(Namespace())
    assert exc_info.value.code == 1
    assert start.call_args.kwargs["status"] == "skipped"


@pytest.mark.parametrize(
    "cmd,args,kind,target",
    [
        (gtfs_pipeline.cmd_ingest, Namespace(agency_id=1, folder="/tmp/x"), "ingest", "pipeline.ingest.ingest"),
        (gtfs_pipeline.cmd_analyze, Namespace(agency_id=1), "analyze", "pipeline.analyze.analyze"),
        (
            gtfs_pipeline.cmd_load_static,
            Namespace(agency_id=1, path="/tmp/x.zip"),
            "static",
            "pipeline.static_loader.load_static",
        ),
    ],
)
def test_a_job_that_runs_opens_and_closes_its_own_row(cmd, args, kind, target):
    conn = _conn()
    with ExitStack() as stack:
        stack.enter_context(patch.object(gtfs_pipeline, "_get_conn", return_value=conn))
        stack.enter_context(patch("pipeline.locks.try_lock_ingest_analyze", return_value=True))
        stack.enter_context(patch("pipeline.clickhouse.get_client", return_value=MagicMock()))
        start = stack.enter_context(patch("pipeline.runs.start_run", return_value=11))
        finish = stack.enter_context(patch("pipeline.runs.finish_run"))
        stack.enter_context(patch(target, return_value=7))
        cmd(args)

    assert start.call_args.args[1] == kind
    assert start.call_args.kwargs["agency_id"] == 1
    assert finish.call_args.args[2] == "ok"


def test_a_failing_job_closes_its_row_as_an_error_and_still_raises():
    conn = _conn()
    with (
        patch.object(gtfs_pipeline, "_get_conn", return_value=conn),
        patch("pipeline.locks.try_lock_ingest_analyze", return_value=True),
        patch("pipeline.clickhouse.get_client", return_value=MagicMock()),
        patch("pipeline.runs.start_run", return_value=11),
        patch("pipeline.runs.finish_run") as finish,
        patch("pipeline.analyze.analyze", side_effect=RuntimeError("bad day")),
    ):
        with pytest.raises(RuntimeError):
            gtfs_pipeline.cmd_analyze(Namespace(agency_id=1))

    assert finish.call_args.args[2] == "error"
    assert "bad day" in finish.call_args.kwargs["error"]


def test_analyze_all_records_one_row_per_agency_and_keeps_going_past_a_failure():
    conn = _conn()
    conn.cursor.return_value.__enter__.return_value.fetchall.return_value = [(1,), (2,)]
    with (
        patch.object(gtfs_pipeline, "_get_conn", return_value=conn),
        patch("pipeline.locks.try_lock_ingest_analyze", return_value=True),
        patch("pipeline.clickhouse.get_client", return_value=MagicMock()),
        patch("pipeline.runs.start_run", return_value=11),
        patch("pipeline.runs.finish_run") as finish,
        patch("pipeline.analyze.analyze", side_effect=[RuntimeError("one"), None]),
    ):
        with pytest.raises(SystemExit):
            gtfs_pipeline.cmd_analyze_all(Namespace())

    assert [call.args[2] for call in finish.call_args_list] == ["error", "ok"]


def test_the_weather_job_records_the_station_days_it_wrote():
    conn = _conn()
    with (
        patch.object(gtfs_pipeline, "_get_conn", return_value=conn),
        patch("pipeline.runs.start_run", return_value=11) as start,
        patch("pipeline.runs.finish_run") as finish,
        patch("pipeline.weather.ingest_weather", return_value=(5, 9, [])),
    ):
        gtfs_pipeline.cmd_ingest_weather(Namespace(days=None))

    assert start.call_args.args[1] == "weather"
    assert finish.call_args.args[2] == "ok"
    assert finish.call_args.kwargs["rows"] == 5
