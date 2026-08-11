"""cmd_ingest_live's all-agencies branch must isolate per-agency failures,
matching cmd_analyze_all's "run-all-then-report" design (see its docstring):
one agency raising (network timeout, a rejected feed_url, a malformed regex)
must not abort every agency scheduled after it in the same run.

DB-free: psycopg2 connection + cursor are mocked, matching
tests/pipeline/test_ingest_live.py's style.
"""

from argparse import Namespace
from unittest.mock import MagicMock, patch

import pytest

import gtfs_pipeline


def _mock_conn_with_agency_ids(ids):
    conn = MagicMock()
    conn.cursor.return_value.__enter__.return_value.fetchall.return_value = [(i,) for i in ids]
    return conn


@pytest.fixture(autouse=True)
def _mock_ch_client():
    """cmd_ingest_live now constructs a ClickHouse client via
    pipeline.clickhouse.get_client() before threading it into ingest_live().
    This module is DB-free (mocked psycopg2 connection/cursor only, per the
    file docstring), so get_client()'s real network call is stubbed out too -
    its return value is irrelevant here since pipeline.ingest.ingest_live is
    itself mocked in every test below."""
    with patch("pipeline.clickhouse.get_client", return_value=MagicMock()):
        yield


def test_ingest_live_all_agencies_continues_past_one_failure():
    conn = _mock_conn_with_agency_ids([1, 2, 3])

    def fake_ingest_live(aid, c, ch):
        if aid == 2:
            raise ValueError("boom")
        return 1

    with patch.object(gtfs_pipeline, "_get_conn", return_value=conn):
        with patch("pipeline.ingest.ingest_live", side_effect=fake_ingest_live) as mock_ingest:
            with pytest.raises(SystemExit):
                gtfs_pipeline.cmd_ingest_live(Namespace(agency_id=None))

    # All three agencies were attempted, not just up to the failing one.
    assert [c.args[0] for c in mock_ingest.call_args_list] == [1, 2, 3]


def test_ingest_live_all_agencies_rolls_back_after_a_failure():
    """The connection must be rolled back after a mid-agency failure, or the
    next agency's first query fails too (psycopg2 aborts the whole
    transaction until a rollback, since ingest_live doesn't do its own)."""
    conn = _mock_conn_with_agency_ids([1, 2])

    def fake_ingest_live(aid, c, ch):
        if aid == 1:
            raise ValueError("boom")
        return 1

    with patch.object(gtfs_pipeline, "_get_conn", return_value=conn):
        with patch("pipeline.ingest.ingest_live", side_effect=fake_ingest_live):
            with pytest.raises(SystemExit):
                gtfs_pipeline.cmd_ingest_live(Namespace(agency_id=None))

    conn.rollback.assert_called_once()


def test_ingest_live_all_agencies_succeeds_when_none_fail():
    conn = _mock_conn_with_agency_ids([1, 2])

    with patch.object(gtfs_pipeline, "_get_conn", return_value=conn):
        with patch("pipeline.ingest.ingest_live", return_value=1) as mock_ingest:
            gtfs_pipeline.cmd_ingest_live(Namespace(agency_id=None))  # must NOT raise

    assert mock_ingest.call_count == 2


def test_ingest_live_all_agencies_logs_success_summary_when_none_fail(caplog):
    """Matches cmd_analyze_all's/cmd_refresh_static's all-succeeded summary
    line - a clean scheduled run should confirm success, not emit only
    per-agency progress lines and nothing at the end."""
    import logging

    conn = _mock_conn_with_agency_ids([1, 2])

    with patch.object(gtfs_pipeline, "_get_conn", return_value=conn):
        with patch("pipeline.ingest.ingest_live", return_value=1):
            with caplog.at_level(logging.INFO):
                gtfs_pipeline.cmd_ingest_live(Namespace(agency_id=None))

    assert "all 2 agencies" in caplog.text
