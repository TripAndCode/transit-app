"""gtfs_pipeline.py's ingest/ingest_live/analyze/analyze_all commands must
all refuse to start when another ingest/analyze process already holds the
shared advisory lock (pipeline.locks.try_lock_ingest_analyze) -- the same
lock api/routers/internal.py's cron fallback endpoint takes, so a scheduled
CLI run and a cron poke can't collide either.

Two-tier degrade on a miss:
- ingest/analyze (single-agency; scripts/fetch_and_ingest.sh and
  docs/deploy-railway.md's Railway sketch both invoke these inside a
  per-agency `for` loop) exit EX_TEMPFAIL (75) -- distinct from a genuine
  failure's exit(1), so the shell loop can skip just this agency this run
  instead of aborting everything after it under `set -euo pipefail`.
- analyze_all/ingest_live (whole-fleet; nothing shell-loops over these) exit
  1, matching their own documented fail-loud contract (CLAUDE.md:
  "partial run can't pass silently").

DB-free: psycopg2 connection is mocked; try_lock_ingest_analyze itself is
tested against a real Postgres connection in tests/pipeline/test_locks.py.
"""

from argparse import Namespace
from unittest.mock import MagicMock, patch

import pytest

import gtfs_pipeline


@pytest.mark.parametrize(
    "cmd,args,target",
    [
        (gtfs_pipeline.cmd_ingest, Namespace(agency_id=1, folder="/tmp/x"), "pipeline.ingest.ingest"),
        (gtfs_pipeline.cmd_analyze, Namespace(agency_id=1), "pipeline.analyze.analyze"),
    ],
)
def test_single_agency_cmd_exits_tempfail_and_never_calls_the_work_when_lock_is_held(cmd, args, target):
    conn = MagicMock()
    with (
        patch.object(gtfs_pipeline, "_get_conn", return_value=conn),
        patch("gtfs_pipeline.try_lock_ingest_analyze", return_value=False),
        patch("pipeline.clickhouse.get_client", return_value=MagicMock()),
        patch(target) as fake_work,
    ):
        with pytest.raises(SystemExit) as exc_info:
            cmd(args)

    assert exc_info.value.code == gtfs_pipeline.EX_TEMPFAIL
    fake_work.assert_not_called()
    conn.close.assert_called_once()


@pytest.mark.parametrize(
    "cmd,args,target",
    [
        (gtfs_pipeline.cmd_analyze_all, Namespace(), "pipeline.analyze.analyze"),
        (gtfs_pipeline.cmd_ingest_live, Namespace(agency_id=1), "pipeline.ingest.ingest_live"),
        (gtfs_pipeline.cmd_ingest_live, Namespace(agency_id=None), "pipeline.ingest.ingest_live"),
    ],
)
def test_whole_fleet_cmd_exits_1_and_never_calls_the_work_when_lock_is_held(cmd, args, target):
    conn = MagicMock()
    with (
        patch.object(gtfs_pipeline, "_get_conn", return_value=conn),
        patch("gtfs_pipeline.try_lock_ingest_analyze", return_value=False),
        patch("pipeline.clickhouse.get_client", return_value=MagicMock()),
        patch(target) as fake_work,
    ):
        with pytest.raises(SystemExit) as exc_info:
            cmd(args)

    assert exc_info.value.code == 1
    fake_work.assert_not_called()
    conn.close.assert_called_once()


@pytest.mark.parametrize(
    "cmd,args,target",
    [
        (gtfs_pipeline.cmd_ingest, Namespace(agency_id=1, folder="/tmp/x"), "pipeline.ingest.ingest"),
        (gtfs_pipeline.cmd_ingest_live, Namespace(agency_id=1), "pipeline.ingest.ingest_live"),
        (gtfs_pipeline.cmd_analyze, Namespace(agency_id=1), "pipeline.analyze.analyze"),
    ],
)
def test_cmd_proceeds_when_lock_is_free(cmd, args, target):
    conn = MagicMock()
    conn.cursor.return_value.__enter__.return_value.fetchall.return_value = [(1, "Agency")]
    with (
        patch.object(gtfs_pipeline, "_get_conn", return_value=conn),
        patch("gtfs_pipeline.try_lock_ingest_analyze", return_value=True),
        patch("pipeline.clickhouse.get_client", return_value=MagicMock()),
        patch(target) as fake_work,
    ):
        cmd(args)

    fake_work.assert_called_once()


def test_cmd_analyze_all_proceeds_when_lock_is_free():
    conn = MagicMock()
    conn.cursor.return_value.__enter__.return_value.fetchall.return_value = [(1,), (2,)]
    with (
        patch.object(gtfs_pipeline, "_get_conn", return_value=conn),
        patch("gtfs_pipeline.try_lock_ingest_analyze", return_value=True),
        patch("pipeline.clickhouse.get_client", return_value=MagicMock()),
        patch("pipeline.analyze.analyze") as fake_analyze,
    ):
        gtfs_pipeline.cmd_analyze_all(Namespace())

    assert fake_analyze.call_count == 2
