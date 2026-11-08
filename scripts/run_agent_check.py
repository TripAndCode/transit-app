#!/usr/bin/env python3
"""Run a check with private logs and compact completion evidence for background tools."""

from __future__ import annotations

import argparse
import json
import os
import signal
import subprocess
import tempfile
import time
from pathlib import Path
from types import FrameType


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cwd", type=Path, required=True)
    parser.add_argument("command", nargs=argparse.REMAINDER)
    args = parser.parse_args()
    command = args.command[1:] if args.command[:1] == ["--"] else args.command
    if not command:
        parser.error("provide a check command after --")
    cwd = args.cwd.resolve()
    if not cwd.is_dir():
        parser.error("--cwd must be an existing directory")

    directory = Path(tempfile.mkdtemp(prefix="transit-agent-check-"))
    log_path = directory / "output.log"
    result_path = directory / "result.json"
    paths = {"log_path": str(log_path), "result_path": str(result_path)}
    print(json.dumps({"status": "running", **paths}), flush=True)
    started = time.monotonic()
    child: subprocess.Popen[bytes] | None = None
    interrupted: int | None = None

    def forward_signal(signum: int, _frame: FrameType | None) -> None:
        nonlocal interrupted
        interrupted = signum
        if child is not None:
            try:
                os.killpg(child.pid, signum)
            except ProcessLookupError:
                pass

    previous = {sig: signal.signal(sig, forward_signal) for sig in (signal.SIGINT, signal.SIGTERM)}
    error: str | None = None
    try:
        with os.fdopen(os.open(log_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "wb") as log:
            try:
                child = subprocess.Popen(
                    command,
                    cwd=cwd,
                    stdin=subprocess.DEVNULL,
                    stdout=log,
                    stderr=subprocess.STDOUT,
                    start_new_session=True,
                )
                if interrupted is not None:
                    forward_signal(interrupted, None)
                returncode = child.wait()
                exit_code = 128 + interrupted if interrupted is not None else returncode
                if exit_code < 0:
                    exit_code = 128 - exit_code
            except OSError as exc:
                error = str(exc)
                exit_code = 127 if isinstance(exc, FileNotFoundError) else 126
    finally:
        for sig, handler in previous.items():
            signal.signal(sig, handler)

    result = {
        "status": "finished",
        "exit_code": exit_code,
        "elapsed_seconds": round(time.monotonic() - started, 3),
        **paths,
    }
    if error is not None:
        result["error"] = error
    with os.fdopen(os.open(result_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "w") as handle:
        json.dump(result, handle)
        handle.write("\n")
    print(json.dumps(result), flush=True)
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
