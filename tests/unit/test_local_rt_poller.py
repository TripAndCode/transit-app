"""`scripts/dev/local_rt_poller.sh` backs off and finally gives up on a
failing `ingest_live`, but not on exit 78: an agency with no timetable loaded
is an operator's step left undone, which polling can never heal, and the run
still ingested every other agency.

A fake `poetry` on PATH exits with the next code from a list, and a fake
`sleep` records each interval instead of waiting.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
EX_CONFIG = 78

_FAKE_POETRY = """#!/usr/bin/env bash
n=$(cat "$POLL_COUNT" 2>/dev/null || echo 0)
echo $((n + 1)) > "$POLL_COUNT"
codes=($POLL_CODES)
exit "${codes[$n]:-1}"
"""

_FAKE_SLEEP = """#!/usr/bin/env bash
echo "$1" >> "$SLEEP_LOG"
"""


def _run(tmp_path: Path, codes: list[int]) -> tuple[int, int, list[str], str]:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    for name, body in (("poetry", _FAKE_POETRY), ("sleep", _FAKE_SLEEP)):
        (bin_dir / name).write_text(body)
        (bin_dir / name).chmod(0o755)
    count, sleeps = tmp_path / "count", tmp_path / "sleeps"
    sleeps.touch()
    result = subprocess.run(
        ["bash", str(ROOT / "scripts" / "dev" / "local_rt_poller.sh")],
        capture_output=True,
        text=True,
        timeout=30,
        env={
            "PATH": f"{bin_dir}{os.pathsep}/usr/bin:/bin",
            "POLL_CODES": " ".join(map(str, codes)),
            "POLL_COUNT": str(count),
            "SLEEP_LOG": str(sleeps),
            "LOCAL_RT_POLL_INTERVAL_SEC": "30",
            "LOCAL_RT_POLL_MAX_FAILURES": "3",
        },
    )
    return result.returncode, int(count.read_text()), sleeps.read_text().split(), result.stderr


def test_a_missing_timetable_keeps_the_normal_interval_and_never_counts_toward_giving_up(tmp_path):
    code, polls, sleeps, stderr = _run(tmp_path, [EX_CONFIG] * 4 + [1, 1, 1])

    assert (code, polls) == (1, 7)
    assert sleeps == ["30", "30", "30", "30", "60", "120"]
    assert "timetable" in stderr


def test_a_missing_timetable_resets_an_earlier_backoff(tmp_path):
    """Exit 78 means every other agency ingested, so the feeds are up."""
    _code, _polls, sleeps, _stderr = _run(tmp_path, [1, EX_CONFIG, 1, 1, 1])

    assert sleeps == ["60", "30", "60", "120"]


def test_a_failing_ingest_still_gives_up(tmp_path):
    code, polls, sleeps, _stderr = _run(tmp_path, [1, 1, 1])

    assert (code, polls) == (1, 3)
    assert sleeps == ["60", "120"]
