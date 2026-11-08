from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "run_agent_check.py"


def run_check(tmp_path: Path, *command: str) -> tuple[subprocess.CompletedProcess[str], dict]:
    completed = subprocess.run(
        [sys.executable, str(SCRIPT), "--cwd", str(tmp_path), "--", *command],
        capture_output=True,
        text=True,
        env={**os.environ, "TMPDIR": str(tmp_path)},
        timeout=10,
    )
    records = [json.loads(line) for line in completed.stdout.splitlines()]
    assert len(records) == 2
    assert records[0]["status"] == "running"
    result = records[1]
    assert json.loads(Path(result["result_path"]).read_text()) == result
    return completed, result


def test_captures_both_streams_privately_and_preserves_cwd(tmp_path: Path):
    cwd = tmp_path / "worktree with spaces"
    cwd.mkdir()
    completed, result = run_check(
        cwd,
        sys.executable,
        "-c",
        "import os, sys; print(os.getcwd()); print('diagnostic', file=sys.stderr)",
    )

    assert completed.returncode == result["exit_code"] == 0
    assert completed.stderr == ""
    log = Path(result["log_path"])
    assert str(cwd) in log.read_text()
    assert "diagnostic" in log.read_text()
    assert "diagnostic" not in completed.stdout
    assert log.stat().st_mode & 0o777 == 0o600
    assert Path(result["result_path"]).stat().st_mode & 0o777 == 0o600
    assert log.parent.stat().st_mode & 0o777 == 0o700


def test_failed_check_keeps_exit_status_even_when_log_says_passed(tmp_path: Path):
    completed, result = run_check(tmp_path, sys.executable, "-c", "print('passed'); raise SystemExit(7)")

    assert completed.returncode == result["exit_code"] == 7
    assert "passed" in Path(result["log_path"]).read_text()


def test_launch_failure_has_a_nonzero_result(tmp_path: Path):
    completed, result = run_check(tmp_path, str(tmp_path / "missing-executable"))

    assert completed.returncode == result["exit_code"] == 127
    assert result["error"]


def test_arguments_are_not_evaluated_by_a_shell(tmp_path: Path):
    marker = tmp_path / "unexpected"
    argument = f"$(touch {marker}); echo injected"
    completed, result = run_check(tmp_path, sys.executable, "-c", "import sys; print(sys.argv[1])", argument)

    assert completed.returncode == 0
    assert Path(result["log_path"]).read_text().strip() == argument
    assert not marker.exists()


@pytest.mark.parametrize("extra", [[], ["--", sys.executable]])
def test_invalid_invocation_does_not_create_artifacts(tmp_path: Path, extra: list[str]):
    cwd = tmp_path if not extra else tmp_path / "missing-directory"
    completed = subprocess.run(
        [sys.executable, str(SCRIPT), "--cwd", str(cwd), *extra],
        capture_output=True,
        text=True,
        env={**os.environ, "TMPDIR": str(tmp_path)},
        timeout=10,
    )

    assert completed.returncode == 2
    assert completed.stdout == ""
    assert not list(tmp_path.glob("transit-agent-check-*"))


def test_cancellation_reaches_descendants_and_is_not_a_pass(tmp_path: Path):
    ready = tmp_path / "ready"
    stopped = tmp_path / "stopped"
    grandchild_code = (
        "import signal, sys, time; from pathlib import Path; "
        f"signal.signal(signal.SIGTERM, lambda *_: (Path({str(stopped)!r}).touch(), sys.exit(0))); "
        f"Path({str(ready)!r}).touch(); time.sleep(30)"
    )
    child_code = (
        "import signal, subprocess, sys; "
        f"child = subprocess.Popen([sys.executable, '-c', {grandchild_code!r}]); "
        "signal.signal(signal.SIGTERM, lambda *_: (child.wait(timeout=5), sys.exit(0))); "
        "child.wait()"
    )
    with subprocess.Popen(
        [sys.executable, str(SCRIPT), "--cwd", str(tmp_path), "--", sys.executable, "-c", child_code],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env={**os.environ, "TMPDIR": str(tmp_path)},
    ) as process:
        try:
            deadline = time.monotonic() + 10
            while not ready.exists() and process.poll() is None and time.monotonic() < deadline:
                time.sleep(0.01)
            assert ready.exists(), "descendant did not start"
            process.send_signal(signal.SIGTERM)
            stdout, stderr = process.communicate(timeout=10)
        finally:
            if process.poll() is None:
                process.kill()
                process.wait()

    result = json.loads(stdout.splitlines()[-1])
    assert process.returncode == result["exit_code"] == 128 + signal.SIGTERM
    assert stopped.exists()
    assert stderr == ""
