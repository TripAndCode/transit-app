"""cmd_refresh_static's all-agencies branch must be fail-loud, matching
cmd_analyze_all and cmd_ingest_live's "run-all-then-report" convention: one
agency's refresh_static failure must not make the whole run exit 0 as if
every agency succeeded.

DB-free: refresh_all is mocked directly (its own per-agency isolation is
covered by tests/pipeline/test_static_fetcher_refresh_all.py).
"""

from argparse import Namespace
from unittest.mock import MagicMock, patch

import pytest

import gtfs_pipeline


def test_refresh_static_all_agencies_exits_nonzero_on_any_failure():
    conn = MagicMock()
    with patch.object(gtfs_pipeline, "_get_conn", return_value=conn):
        with patch("pipeline.static_fetcher.refresh_all", return_value=(1, [42])):
            with pytest.raises(SystemExit):
                gtfs_pipeline.cmd_refresh_static(Namespace(agency_id=None, dest="/tmp/x"))


def test_refresh_static_all_agencies_succeeds_when_none_fail():
    conn = MagicMock()
    with patch.object(gtfs_pipeline, "_get_conn", return_value=conn):
        with patch("pipeline.static_fetcher.refresh_all", return_value=(2, [])):
            gtfs_pipeline.cmd_refresh_static(Namespace(agency_id=None, dest="/tmp/x"))  # must NOT raise
