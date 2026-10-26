"""`gtfs_pipeline.py reprobe_rt_coverage`: the scheduled entry point for the
RT field-coverage renewal, for a deployment whose daily job is a command
rather than a poke of the cron endpoint."""

from __future__ import annotations

import logging
from argparse import Namespace
from unittest.mock import MagicMock, patch

import pytest

import gtfs_pipeline
from pipeline import rt_coverage_reprobe
from pipeline.rt_coverage_reprobe import ReprobeOutcome


def _run(outcome: ReprobeOutcome) -> list[tuple]:
    conn = MagicMock()
    calls: list[tuple] = []

    def fake_reprobe(c, db_url, **_kw):
        calls.append((c, db_url))
        return outcome

    with (
        patch.object(gtfs_pipeline, "_get_conn", return_value=conn),
        patch.object(rt_coverage_reprobe, "reprobe_due_agencies", side_effect=fake_reprobe),
    ):
        try:
            gtfs_pipeline.cmd_reprobe_rt_coverage(Namespace())
        finally:
            conn.close.assert_called_once()
    assert calls == [(conn, gtfs_pipeline.DATABASE_URL)]
    return calls


def test_reports_what_it_recorded_and_skipped(caplog):
    with caplog.at_level(logging.INFO):
        _run(ReprobeOutcome(recorded=[8, 9], skipped=[54]))
    assert "recorded 2 agency(ies) [8, 9]" in caplog.text
    assert "skipped 1 [54]" in caplog.text


def test_nothing_due_is_a_quiet_success(caplog):
    with caplog.at_level(logging.INFO):
        _run(ReprobeOutcome())
    assert "nothing due" in caplog.text


def test_a_thin_poll_is_not_a_failure():
    """The next run tries again; the command must not page anyone for it."""
    _run(ReprobeOutcome(skipped=[54]))


def test_a_failed_agency_fails_the_command(caplog):
    with pytest.raises(SystemExit) as exc, caplog.at_level(logging.INFO):
        _run(ReprobeOutcome(recorded=[8], failed=[9]))
    assert exc.value.code == 1
    assert "failed 1 [9]" in caplog.text
