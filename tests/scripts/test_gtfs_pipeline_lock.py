"""gtfs_pipeline.py's ingest/ingest_live/analyze/analyze_all commands must
all skip (return normally, never call the underlying work) when another
ingest/analyze process already holds the shared advisory lock
(pipeline.locks.try_lock_ingest_analyze) -- the same lock
api/routers/internal.py's cron fallback endpoint takes, so a scheduled CLI
run and a cron poke can't collide either.

Skip-not-exit, matching the cron endpoint's degrade: production invokes
these as separate per-agency processes under `set -euo pipefail`
(scripts/fetch_and_ingest.sh) -- a nonzero exit on lock contention for one
agency would abort the whole remaining loop, a worse outcome than the
collision the lock exists to prevent.

Not DB-free: this module lives outside tests/unit/, so it inherits the
session-scoped apply_schema fixture (tests/conftest.py) even though
psycopg2 itself is mocked here; try_lock_ingest_analyze's real-Postgres
behavior is covered separately in tests/pipeline/test_locks.py.
"""

from argparse import Namespace
from unittest.mock import MagicMock, patch

import pytest

import gtfs_pipeline


@pytest.mark.parametrize(
    "cmd,args,target",
    [
        (gtfs_pipeline.cmd_ingest, Namespace(agency_id=1, folder="/tmp/x"), "pipeline.ingest.ingest"),
        (gtfs_pipeline.cmd_ingest_live, Namespace(agency_id=1), "pipeline.ingest.ingest_live"),
        (gtfs_pipeline.cmd_ingest_live, Namespace(agency_id=None), "pipeline.ingest.ingest_live"),
        (gtfs_pipeline.cmd_analyze, Namespace(agency_id=1), "pipeline.analyze.analyze"),
        (gtfs_pipeline.cmd_analyze_all, Namespace(), "pipeline.analyze.analyze"),
    ],
)
def test_cmd_skips_and_never_calls_the_work_when_lock_is_held(cmd, args, target):
    conn = MagicMock()
    with (
        patch.object(gtfs_pipeline, "_get_conn", return_value=conn),
        patch("gtfs_pipeline.try_lock_ingest_analyze", return_value=False),
        patch("pipeline.clickhouse.get_client", return_value=MagicMock()),
        patch(target) as fake_work,
    ):
        cmd(args)  # must return normally -- not sys.exit -- so a shell `for` loop over agencies keeps going

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
