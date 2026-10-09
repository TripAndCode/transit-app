"""scripts/run_full_ci.sh waits for the ClickHouse the schema step talks to.

Driven through shimmed `docker` and `poetry` plus a fake ClickHouse on the
port the script publishes, rather than asserted against the script's text.
The fake reproduces the official image's first run: its init server answers
inside the container (so every `docker exec` probe succeeds) while the
published port still returns an empty reply, because that server listens on
the container's loopback only. The shimmed schema step then makes a real
HTTP request through that port, which is the connection that fails when
readiness is declared too early.
"""

from __future__ import annotations

import http.server
import itertools
import os
import subprocess
import sys
import textwrap
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "run_full_ci.sh"

# Requests through the published port that land while only the init server
# runs. Counted rather than timed, so a script that trusts the in-container
# probe fails at its schema step however fast or slow the host is.
INIT_WINDOW_REQUESTS = 1


def _write_executable(path: Path, body: str) -> None:
    path.write_text(textwrap.dedent(body))
    path.chmod(0o755)


def _install_shims(bin_dir: Path, port_file: Path, calls_log: Path) -> None:
    _write_executable(
        bin_dir / "docker",
        f"""\
        #!/usr/bin/env bash
        case "$1" in
          build) echo "sha256:fake-db-image" ;;
          run)
            for arg in "$@"; do
              case "$arg" in
                127.0.0.1:*:8123) port="${{arg#127.0.0.1:}}"; echo "${{port%:8123}}" > "{port_file}" ;;
              esac
            done
            ;;
          # The init server answers every in-container probe.
          exec|rm|logs) ;;
          *) echo "unexpected docker subcommand: $1" >&2; exit 1 ;;
        esac
        """,
    )
    _write_executable(
        bin_dir / "poetry",
        f"""\
        #!/usr/bin/env bash
        echo "$*" >> "{calls_log}"
        case "$*" in
          *apply_schema*)
            exec "{sys.executable}" -c '
        import os, urllib.request
        url = "http://" + os.environ["CLICKHOUSE_HOST"] + ":" + os.environ["CLICKHOUSE_PORT"] + "/ping"
        urllib.request.urlopen(url, timeout=5).read()
        ' ;;
        esac
        """,
    )


def _serve_like_a_fresh_clickhouse(port: int) -> http.server.ThreadingHTTPServer:
    arrivals = itertools.count()
    lock = threading.Lock()

    class Handler(http.server.BaseHTTPRequestHandler):
        def _answer(self) -> None:
            with lock:
                arrival = next(arrivals)
            if arrival < INIT_WINDOW_REQUESTS:
                # What the published port gives while only the init server
                # runs: the connection is accepted and closed unanswered.
                self.close_connection = True
                return
            body = b"1\n"
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        do_GET = _answer
        do_POST = _answer

        def log_message(self, format: str, *args: object) -> None:
            pass

    server = http.server.ThreadingHTTPServer(("127.0.0.1", port), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


def test_schema_step_waits_out_the_clickhouse_init_server(tmp_path: Path) -> None:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    port_file = tmp_path / "ch_port"
    calls_log = tmp_path / "poetry_calls"
    _install_shims(bin_dir, port_file, calls_log)

    env = {k: v for k, v in os.environ.items() if k != "COVERAGE"}
    env["PATH"] = f"{bin_dir}{os.pathsep}{env.get('PATH', '')}"
    proc = subprocess.Popen(
        ["bash", str(SCRIPT)],
        cwd=ROOT,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    server = None
    try:
        deadline = time.monotonic() + 30
        while not port_file.exists() or not port_file.read_text().strip():
            assert proc.poll() is None, proc.communicate()[0]
            assert time.monotonic() < deadline, "script never started the ClickHouse container"
            time.sleep(0.05)
        server = _serve_like_a_fresh_clickhouse(int(port_file.read_text()))
        output, _ = proc.communicate(timeout=90)
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.communicate()
        if server is not None:
            server.shutdown()
            server.server_close()

    assert proc.returncode == 0, output
    calls = calls_log.read_text().splitlines()
    assert sum("apply_schema" in call for call in calls) == 1, calls
    assert calls[-1].startswith("run pytest"), calls
