"""`scripts/fetch_archives.sh` against a local stand-in for the collector
(its `FETCH_TEST_LOCAL` hook rsyncs local paths, no ssh)."""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "fetch_archives.sh"
DAYS = ("20260915", "20260916", "20260917", "20261001")

pytestmark = pytest.mark.skipif(shutil.which("rsync") is None, reason="rsync is not installed")


def _run(tmp_path: Path, **env: str) -> subprocess.CompletedProcess[str]:
    remote = tmp_path / "remote"
    for day in DAYS:
        (remote / "8" / "rt").mkdir(parents=True, exist_ok=True)
        (remote / "8" / "rt" / f"{day}.tar.gz").write_bytes(day.encode())
    (remote / "8" / "static").mkdir(parents=True, exist_ok=True)
    (remote / "8" / "static" / "gtfs_static_20261014.zip").write_bytes(b"zip")
    full_env = {
        **{k: v for k, v in os.environ.items() if k != "SINCE"},
        "FETCH_TEST_LOCAL": "1",
        "ORACLE_HOST": "collector.invalid",
        "ORACLE_SSH_KEY_PATH": os.devnull,
        "COLLECTOR_DATA_DIR": str(remote),
        "AGENCY_IDS": "8",
        "LOCAL_RT_DIR": str(tmp_path / "rt"),
        "LOCAL_STATIC_DIR": str(tmp_path / "static"),
        **env,
    }
    return subprocess.run(["bash", str(SCRIPT)], env=full_env, capture_output=True, text=True, check=False)


def _fetched(tmp_path: Path) -> list[str]:
    return sorted(p.name for p in (tmp_path / "rt" / "8").glob("*.tar.gz"))


def test_without_since_every_archived_day_is_fetched(tmp_path):
    result = _run(tmp_path)
    assert result.returncode == 0, result.stderr
    assert _fetched(tmp_path) == [f"{d}.tar.gz" for d in DAYS]


def test_since_fetches_only_that_day_and_later(tmp_path):
    result = _run(tmp_path, SINCE="20260917")
    assert result.returncode == 0, result.stderr
    assert _fetched(tmp_path) == ["20260917.tar.gz", "20261001.tar.gz"]
    assert (tmp_path / "static" / "8" / "gtfs_static_20261014.zip").exists(), "timetables still come"


def test_since_later_than_every_archive_fetches_none_and_succeeds(tmp_path):
    result = _run(tmp_path, SINCE="20270101")
    assert result.returncode == 0, result.stderr
    assert _fetched(tmp_path) == []


def test_a_malformed_since_is_refused_before_anything_is_fetched(tmp_path):
    result = _run(tmp_path, SINCE="2026-09-17")
    assert result.returncode != 0
    assert "SINCE" in result.stderr
    assert not (tmp_path / "rt" / "8").exists() or _fetched(tmp_path) == []
