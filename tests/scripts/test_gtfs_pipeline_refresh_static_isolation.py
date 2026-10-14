"""cmd_refresh_static's all-agencies branch must be fail-loud, matching
cmd_analyze_all and cmd_ingest_live's "run-all-then-report" convention: one
agency's refresh_static failure must not make the whole run exit 0 as if
every agency succeeded. ``--force`` reaches every reload the command runs.

DB-free: refresh_all, or the refresh_static each reload calls, is mocked
directly (refresh_all's own per-agency isolation is covered by
tests/pipeline/test_static_fetcher_refresh_all.py).
"""

from argparse import Namespace
from unittest.mock import MagicMock, patch

import pytest

import gtfs_pipeline


def test_refresh_static_all_agencies_exits_nonzero_on_any_failure():
    conn = MagicMock()
    with patch.object(gtfs_pipeline, "_get_conn", return_value=conn):
        with patch("pipeline.static_fetcher.refresh_all", return_value=(1, 2, [42])):
            with pytest.raises(SystemExit):
                gtfs_pipeline.cmd_refresh_static(Namespace(agency_id=None, dest="/tmp/x", force=False))


def test_refresh_static_all_agencies_failure_log_reports_n_of_m(caplog):
    """Matches analyze-all's/ingest-live's 'N of M agencies failed' shape,
    so the three scheduled all-agencies commands emit an identical
    operator/alerting-facing log line rather than refresh-static's own
    denominator-less format."""
    conn = MagicMock()
    with patch.object(gtfs_pipeline, "_get_conn", return_value=conn):
        with patch("pipeline.static_fetcher.refresh_all", return_value=(1, 3, [42])):
            with pytest.raises(SystemExit):
                gtfs_pipeline.cmd_refresh_static(Namespace(agency_id=None, dest="/tmp/x", force=False))
    assert "1 of 3 agencies failed" in caplog.text


def test_refresh_static_all_agencies_succeeds_when_none_fail():
    conn = MagicMock()
    with patch.object(gtfs_pipeline, "_get_conn", return_value=conn):
        with patch("pipeline.static_fetcher.refresh_all", return_value=(2, 2, [])):
            gtfs_pipeline.cmd_refresh_static(Namespace(agency_id=None, dest="/tmp/x", force=False))  # must NOT raise


@pytest.mark.parametrize(("flags", "forced"), [([], False), (["--force"], True)])
def test_refresh_static_force_reaches_one_agencys_reload(monkeypatch, flags, forced):
    monkeypatch.setattr("sys.argv", ["gtfs_pipeline.py", "refresh-static", "--agency-id", "8", *flags])
    with (
        patch.object(gtfs_pipeline, "_get_conn", return_value=MagicMock()),
        patch("pipeline.static_fetcher.refresh_static", return_value=None) as refresh,
    ):
        gtfs_pipeline.main()
    assert [(c.args[0], c.kwargs["force"]) for c in refresh.call_args_list] == [(8, forced)]


@pytest.mark.parametrize(("flags", "forced"), [([], False), (["--force"], True)])
def test_refresh_static_force_reaches_every_agencys_reload(monkeypatch, flags, forced):
    conn = MagicMock()
    conn.cursor.return_value.__enter__.return_value.fetchall.return_value = [(8,), (9,)]
    monkeypatch.setattr("sys.argv", ["gtfs_pipeline.py", "refresh-static", *flags])
    with (
        patch.object(gtfs_pipeline, "_get_conn", return_value=conn),
        patch("pipeline.static_fetcher.refresh_static", return_value=None) as refresh,
    ):
        gtfs_pipeline.main()
    assert [(c.args[0], c.kwargs["force"]) for c in refresh.call_args_list] == [(8, forced), (9, forced)]
