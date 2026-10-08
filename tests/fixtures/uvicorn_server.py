"""Run the real FastAPI app as a uvicorn subprocess for browser-driven tests."""

from __future__ import annotations

import os
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request
from collections.abc import Iterator, Mapping
from contextlib import contextmanager

import pytest

# Generous because startup can load the sentence-transformers embedder (the
# i18n scan enables ASK_INTENT_CACHE_ENABLED), which then dominates it: even
# with the model already in the HuggingFace cache it revalidates revisions over
# the network before loading, and on a cold cache it downloads the model first.
# A tight bound fails as "server did not start" on a slow link or a loaded
# runner, pointing at the wrong thing entirely.
STARTUP_TIMEOUT_SEC = 180


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@contextmanager
def start_uvicorn(extra_env: Mapping[str, str] | None = None) -> Iterator[str]:
    """Start `api.main:app` on a free port and yield its base URL.

    Fails the calling test with the server's captured output if it does not
    answer within `STARTUP_TIMEOUT_SEC`.
    """
    port = _free_port()
    # Keep the server's output instead of discarding it: a startup that dies
    # (a missing provider key, an unreachable DB) is otherwise indistinguishable
    # from one that is merely slow, and both surface as the timeout. A file, not
    # a PIPE, so a chatty startup cannot fill the buffer and wedge; unnamed and
    # closed on the way out because the nightly runner is persistent, so a
    # leaked temp file per run accumulates there forever.
    with tempfile.TemporaryFile("w+") as log:
        proc = subprocess.Popen(
            # Not `poetry run`: it resolves its venv by cwd, so from a worktree it
            # starts an interpreter without the project and this reports a
            # startup timeout instead of the real cause.
            [sys.executable, "-m", "uvicorn", "api.main:app", "--port", str(port), "--no-access-log"],
            env={**os.environ, **(extra_env or {})},
            stdout=log,
            stderr=subprocess.STDOUT,
        )
        try:
            base_url = f"http://127.0.0.1:{port}"
            deadline = time.time() + STARTUP_TIMEOUT_SEC
            while True:
                try:
                    urllib.request.urlopen(f"{base_url}/", timeout=1)
                    break
                except Exception:
                    if time.time() >= deadline:
                        log.seek(0)
                        pytest.fail(
                            f"API server did not start within {STARTUP_TIMEOUT_SEC} seconds. "
                            f"Server output:\n{log.read()}"
                        )
                    time.sleep(0.5)
            yield base_url
        finally:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait()
