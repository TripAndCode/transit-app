"""`gtfs_pipeline.py prune-updates`: the prune decides whether to take the
ingest/analyze lock, and the command supplies how a busy lock ends it."""

from __future__ import annotations

import logging
from argparse import Namespace
from datetime import date
from unittest.mock import MagicMock, patch

import pytest

import gtfs_pipeline
from pipeline import clickhouse, retention
from pipeline.retention import PartitionVerdict, PruneResult, UpdatesPartition

_VERDICTS = [
    PartitionVerdict(UpdatesPartition("202301", 5, 50, (1,)), date(2023, 2, 3), None),
    PartitionVerdict(UpdatesPartition("202302", 6, 60, (1,)), date(2023, 3, 3), "agency 1 has no aggregates yet"),
]


def _run(*, dry_run: bool, enabled: bool, lock: bool = True) -> tuple[list[dict], bool]:
    conn = MagicMock()
    calls: list[dict] = []

    def fake_prune(c, ch, *, acquire, **kw):
        calls.append(kw)
        if not dry_run and not enabled:
            return PruneResult(False, [], [])
        if not dry_run:
            acquire()
        return PruneResult(enabled, _VERDICTS, [] if dry_run else ["202301"])

    with (
        patch.object(gtfs_pipeline, "_get_conn", return_value=conn),
        patch.object(gtfs_pipeline, "try_lock_ingest_analyze_timed", return_value=(lock, 3)) as locked,
        patch.object(gtfs_pipeline.pipeline_runs, "start_run"),
        patch.object(clickhouse, "get_client", return_value=object()),
        patch.object(retention, "prune_updates", side_effect=fake_prune),
    ):
        gtfs_pipeline.cmd_prune_updates(Namespace(dry_run=dry_run, days=400))
    return calls, locked.called


def test_a_dry_run_lists_drops_and_keeps_without_the_lock(caplog):
    with caplog.at_level(logging.INFO):
        calls, locked = _run(dry_run=True, enabled=False)
    assert calls == [{"days": 400, "dry_run": True}]
    assert not locked
    assert "would drop 202301 (5 rows, 50 bytes); agencies 1 keep their aggregates before 2023-02-03" in caplog.text
    assert "keeping 202302: agency 1 has no aggregates yet" in caplog.text


def test_a_run_that_may_drop_takes_the_lock(caplog):
    with caplog.at_level(logging.INFO):
        _, locked = _run(dry_run=False, enabled=True)
    assert locked
    assert "dropped 202301" in caplog.text


def test_a_switched_off_run_takes_no_lock_and_logs_no_verdict(caplog):
    with caplog.at_level(logging.INFO):
        calls, locked = _run(dry_run=False, enabled=False)
    assert calls == [{"days": 400, "dry_run": False}]
    assert not locked
    assert "202301" not in caplog.text


def test_a_busy_lock_ends_the_process():
    with pytest.raises(SystemExit) as exc:
        _run(dry_run=False, enabled=True, lock=False)
    assert exc.value.code == 1
