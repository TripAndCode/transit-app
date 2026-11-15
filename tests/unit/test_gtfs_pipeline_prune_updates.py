"""`gtfs_pipeline.py prune-updates`: a dry run reads without the lock, and a
run that may drop takes the ingest/analyze lock first."""

from __future__ import annotations

import logging
from argparse import Namespace
from unittest.mock import MagicMock, patch

import pytest

import gtfs_pipeline
from pipeline import clickhouse, retention
from pipeline.retention import PartitionVerdict, PruneResult, UpdatesPartition

_VERDICTS = [
    PartitionVerdict(UpdatesPartition("202301", 5, 50, (1,)), None),
    PartitionVerdict(UpdatesPartition("202302", 6, 60, (1,)), "agency 1 has aggregates from 2023-02-10"),
]


def _run(*, dry_run: bool, enabled: bool, lock: bool = True) -> list[dict]:
    conn = MagicMock()
    calls: list[dict] = []

    def fake_prune(c, ch, **kw):
        calls.append(kw)
        return PruneResult(enabled, _VERDICTS if (dry_run or enabled) else [], [])

    with (
        patch.object(gtfs_pipeline, "_get_conn", return_value=conn),
        patch.object(gtfs_pipeline, "try_lock_ingest_analyze_timed", return_value=(lock, 3)) as locked,
        patch.object(gtfs_pipeline.pipeline_runs, "start_run"),
        patch.object(clickhouse, "get_client", return_value=object()),
        patch.object(retention, "prune_updates", side_effect=fake_prune),
        patch.object(retention, "updates_prune_enabled", return_value=enabled),
    ):
        gtfs_pipeline.cmd_prune_updates(Namespace(dry_run=dry_run, days=400))
        assert locked.called is (not dry_run and enabled)
    return calls


def test_a_dry_run_lists_drops_and_keeps_without_the_lock(caplog):
    with caplog.at_level(logging.INFO):
        calls = _run(dry_run=True, enabled=False)
    assert calls == [{"days": 400, "dry_run": True}]
    assert "would drop 202301 (5 rows, 50 bytes)" in caplog.text
    assert "keeping 202302: agency 1 has aggregates from 2023-02-10" in caplog.text


def test_a_run_that_may_drop_takes_the_lock(caplog):
    with caplog.at_level(logging.INFO):
        _run(dry_run=False, enabled=True)
    assert "dropped 202301" in caplog.text


def test_a_switched_off_run_takes_no_lock():
    assert _run(dry_run=False, enabled=False) == [{"days": 400, "dry_run": False}]


def test_a_busy_lock_refuses_before_reading_anything():
    with pytest.raises(SystemExit) as exc:
        _run(dry_run=False, enabled=True, lock=False)
    assert exc.value.code == 1
