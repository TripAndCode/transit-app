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
        **{k: v for k, v in os.environ.items() if k not in ("SINCE", "ORACLE_SSH_KEY")},
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
    assert result.returncode == 2
    assert "SINCE" in result.stderr
    assert not (tmp_path / "rt").exists(), "refused before any local directory is made"


def _remote_agency(tmp_path: Path, agency: str, *, rt: bool = True) -> None:
    base = tmp_path / "remote" / agency
    base.mkdir(parents=True, exist_ok=True)
    if rt:
        (base / "rt").mkdir(exist_ok=True)
        (base / "rt" / "20261001.tar.gz").write_bytes(agency.encode())


def test_an_agency_the_collector_does_not_poll_is_skipped_and_later_ones_still_come(tmp_path):
    """AGENCY_IDS defaults to every agencies.csv row with a feed_url, but the VM holds only the
    agencies it polls."""
    _remote_agency(tmp_path, "1")
    skipped = tmp_path / "skipped"
    result = _run(tmp_path, AGENCY_IDS="1 11 8", SKIPPED_AGENCIES_FILE=str(skipped))
    assert result.returncode == 0, result.stderr
    assert (tmp_path / "rt" / "1" / "20261001.tar.gz").exists()
    assert _fetched(tmp_path) == [f"{d}.tar.gz" for d in DAYS], "agency 8, after the absent 11, is still fetched"
    assert "[a11] not collected" in result.stdout
    assert skipped.read_text().split() == ["11"], "the caller learns which ids were skipped"


def test_an_absent_agency_is_skipped_under_since_too(tmp_path):
    result = _run(tmp_path, AGENCY_IDS="11 8", SINCE="20261001")
    assert result.returncode == 0, result.stderr
    assert _fetched(tmp_path) == ["20261001.tar.gz"]


def test_a_failed_fetch_is_reported_after_every_other_agency_is_fetched(tmp_path):
    """An agency the VM holds whose RT fetch fails is a real failure, but not one that stops the rest."""
    _remote_agency(tmp_path, "9", rt=False)
    result = _run(tmp_path, AGENCY_IDS="9 8")
    assert result.returncode == 1
    assert "RT fetch failed for agencies: 9" in result.stderr
    assert _fetched(tmp_path) == [f"{d}.tar.gz" for d in DAYS]


def test_a_data_directory_holding_none_of_the_agencies_is_an_error_not_a_quiet_skip(tmp_path):
    """A wrong COLLECTOR_DATA_DIR must not read as "nothing is collected" and succeed."""
    (tmp_path / "empty").mkdir()
    result = _run(tmp_path, COLLECTOR_DATA_DIR=str(tmp_path / "empty"))
    assert result.returncode == 1
    assert "holds none of" in result.stderr


def test_an_unreadable_data_directory_is_named(tmp_path):
    result = _run(tmp_path, COLLECTOR_DATA_DIR=str(tmp_path / "missing"))
    assert result.returncode == 1
    assert "could not list" in result.stderr
