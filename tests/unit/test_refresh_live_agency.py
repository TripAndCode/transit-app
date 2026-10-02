"""/delays/refresh's collector path stores the poll as a live payload, under
the same name the collector's own push gives it, so whichever arrives second
is absorbed by the live path's file-level dedup."""

import subprocess
from unittest.mock import MagicMock, patch

from api.routers.map import _ingest_live_agency


def test_the_collector_file_is_stored_as_a_live_payload_under_the_push_name(monkeypatch, tmp_path):
    key = tmp_path / "id"
    key.write_text("k")
    monkeypatch.setenv("DATABASE_URL", "postgresql://fake")
    monkeypatch.setenv("ORACLE_HOST", "vm.example")
    monkeypatch.setenv("ORACLE_SSH_KEY_PATH", str(key))
    monkeypatch.setenv("COLLECTOR_DATA_DIR", "/data")
    listing = subprocess.CompletedProcess([], 0, stdout="1790910900.0 /data/7/rt/20261002/TripUpdate_031500.pb\n")
    payload = subprocess.CompletedProcess([], 0, stdout=b"raw-pb")
    with (
        patch("psycopg2.connect", return_value=MagicMock()),
        patch("pipeline.locks.try_lock_ingest_analyze", return_value=True),
        patch("pipeline.clickhouse.get_client", return_value=MagicMock()),
        patch("subprocess.run", side_effect=[listing, payload]),
        patch("pipeline.ingest.ingest_live_payload", return_value=5) as live,
        patch("pipeline.ingest.ingest") as archive,
    ):
        assert _ingest_live_agency(7) == 5
    archive.assert_not_called()
    agency, raw, captured_at, file_name = live.call_args.args[:4]
    assert (agency, raw, file_name) == (7, b"raw-pb", "oracle/20261002/TripUpdate_031500.pb")
    assert captured_at == "2026-10-02T03:15:00+00:00"
