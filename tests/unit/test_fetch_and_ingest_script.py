"""`scripts/fetch_and_ingest.sh` ingests each agency's archives before it
loads that agency's newest local timetable: a load replaces the timetable
wholesale, and archived days join the one loaded when they are read. Only an
agency ingest refuses for having no timetable loaded (exit 78) gets the
local timetable loaded first and its ingest retried once.

The script runs from a copy in a temporary tree, with `fetch_archives.sh`
stubbed and a fake `poetry` on PATH that logs each pipeline command and exits
with the codes a test asks for, one per call, so no network or database is
touched.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
EX_CONFIG = 78

_FAKE_POETRY = """#!/usr/bin/env bash
# poetry run python <repo>/gtfs_pipeline.py <command> <path> --agency-id <id>
cmd="$4"
id=""
prev=""
for a in "$@"; do
    [ "$prev" = "--agency-id" ] && id="$a"
    prev="$a"
done
echo "$cmd $id" >> "$PIPELINE_LOG"
var="STUB_EXIT_${cmd}_${id}"
codes=(${!var:-0})
count_file="$PIPELINE_LOG.$cmd.$id"
n=$(cat "$count_file" 2>/dev/null || echo 0)
echo $((n + 1)) > "$count_file"
exit "${codes[$n]:-${codes[${#codes[@]}-1]}}"
"""


def _tree(tmp_path: Path, static_zip_dirs: list[str]) -> Path:
    scripts = tmp_path / "repo" / "scripts"
    scripts.mkdir(parents=True)
    shutil.copy(ROOT / "scripts" / "fetch_and_ingest.sh", scripts / "fetch_and_ingest.sh")
    (scripts / "fetch_archives.sh").write_text("exit 0\n")
    for rel in static_zip_dirs:
        d = tmp_path / "repo" / rel
        d.mkdir(parents=True)
        (d / "gtfs_static_20260101.zip").write_bytes(b"")
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    poetry = bin_dir / "poetry"
    poetry.write_text(_FAKE_POETRY)
    poetry.chmod(0o755)
    return tmp_path


def _run(tmp_path: Path, **env: str) -> tuple[subprocess.CompletedProcess[str], list[str]]:
    log = tmp_path / "pipeline.log"
    log.touch()
    result = subprocess.run(
        ["bash", str(tmp_path / "repo" / "scripts" / "fetch_and_ingest.sh")],
        capture_output=True,
        text=True,
        timeout=30,
        env={"PATH": f"{tmp_path / 'bin'}{os.pathsep}/usr/bin:/bin", "PIPELINE_LOG": str(log), **env},
    )
    return result, log.read_text().splitlines()


def test_each_agency_ingests_its_archives_before_loading_its_newest_timetable(tmp_path):
    _tree(tmp_path, ["raw_archives_static/8", "raw_archives_static/9"])

    result, calls = _run(tmp_path, COLLECTOR_DATA_DIR="/data", AGENCY_IDS="8 9")

    assert result.returncode == 0, result.stdout + result.stderr
    assert calls == [
        "ingest 8",
        "load_static 8",
        "analyze 8",
        "ingest 9",
        "load_static 9",
        "analyze 9",
    ]


def test_an_agency_with_no_timetable_loaded_gets_its_local_one_and_one_retry(tmp_path):
    _tree(tmp_path, ["raw_archives_static/9"])

    result, calls = _run(
        tmp_path, COLLECTOR_DATA_DIR="/data", AGENCY_IDS="9 10", **{"STUB_EXIT_ingest_9": f"{EX_CONFIG} 0"}
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert calls == ["ingest 9", "load_static 9", "ingest 9", "analyze 9", "ingest 10", "analyze 10"]


def test_an_agency_with_no_timetable_anywhere_is_skipped_and_reported_at_the_end(tmp_path):
    _tree(tmp_path, [])

    result, calls = _run(
        tmp_path, COLLECTOR_DATA_DIR="/data", AGENCY_IDS="8 9 10", **{"STUB_EXIT_ingest_9": str(EX_CONFIG)}
    )

    assert calls == ["ingest 8", "analyze 8", "ingest 9", "ingest 10", "analyze 10"]
    assert result.returncode == EX_CONFIG
    assert "9" in result.stderr.splitlines()[-1]


def test_a_retry_still_refused_is_skipped_too(tmp_path):
    _tree(tmp_path, ["raw_archives_static/9"])

    result, calls = _run(tmp_path, COLLECTOR_DATA_DIR="/data", AGENCY_IDS="9", **{"STUB_EXIT_ingest_9": str(EX_CONFIG)})

    assert calls == ["ingest 9", "load_static 9", "ingest 9"]
    assert result.returncode == EX_CONFIG


def test_a_genuine_ingest_failure_still_stops_the_run(tmp_path):
    _tree(tmp_path, [])

    result, calls = _run(tmp_path, COLLECTOR_DATA_DIR="/data", AGENCY_IDS="8 9", **{"STUB_EXIT_ingest_8": "1"})

    assert calls == ["ingest 8"]
    assert result.returncode == 1


def test_an_agency_the_collector_does_not_hold_is_not_ingested(tmp_path):
    """fetch_archives.sh names the ids it skipped, so they never reach ingest's no-timetable refusal."""
    _tree(tmp_path, [])
    (tmp_path / "repo" / "scripts" / "fetch_archives.sh").write_text('echo 11 >> "$SKIPPED_AGENCIES_FILE"\n')

    result, calls = _run(tmp_path, COLLECTOR_DATA_DIR="/data", AGENCY_IDS="8 11 9")

    assert result.returncode == 0, result.stdout + result.stderr
    assert calls == ["ingest 8", "analyze 8", "ingest 9", "analyze 9"]


def test_a_failed_fetch_still_ingests_every_agency_and_fails_the_run_at_the_end(tmp_path):
    _tree(tmp_path, [])
    (tmp_path / "repo" / "scripts" / "fetch_archives.sh").write_text("exit 1\n")

    result, calls = _run(tmp_path, COLLECTOR_DATA_DIR="/data", AGENCY_IDS="8 9")

    assert calls == ["ingest 8", "analyze 8", "ingest 9", "analyze 9"]
    assert result.returncode == 1
    assert "fetch" in result.stderr.splitlines()[-1]


def test_the_single_agency_path_ingests_before_loading_the_timetable(tmp_path):
    _tree(tmp_path, ["raw_archives_static"])

    result, calls = _run(tmp_path, AGENCY_ID="1")

    assert result.returncode == 0, result.stdout + result.stderr
    assert calls == ["ingest 1", "load_static 1", "analyze 1"]


def test_the_single_agency_path_retries_once_with_the_local_timetable(tmp_path):
    _tree(tmp_path, ["raw_archives_static"])

    result, calls = _run(tmp_path, AGENCY_ID="1", **{"STUB_EXIT_ingest_1": f"{EX_CONFIG} 0"})

    assert result.returncode == 0, result.stdout + result.stderr
    assert calls == ["ingest 1", "load_static 1", "ingest 1", "analyze 1"]
