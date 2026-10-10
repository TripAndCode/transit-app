"""tools/geosql/bootstrap.sh derives Dekart's connection from DATABASE_URL.

The script is driven with a stub `curl`, so no Dekart container is needed.
"""

from __future__ import annotations

import os
import subprocess
import tempfile
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[2] / "tools" / "geosql" / "bootstrap.sh"


def _run(env_extra: dict[str, str], *, curl_ok: bool = True) -> subprocess.CompletedProcess:
    with tempfile.TemporaryDirectory() as tmp:
        stub = Path(tmp) / "curl"
        stub.write_text(f"#!/bin/sh\nexit {0 if curl_ok else 22}\n")
        stub.chmod(0o755)
        env = {k: v for k, v in os.environ.items() if k != "DATABASE_URL"}
        env["PATH"] = f"{tmp}:{env['PATH']}"
        env.update(env_extra)
        return subprocess.run(["bash", str(SCRIPT)], env=env, capture_output=True, text=True, timeout=30)


def test_connection_follows_database_url_port_and_is_read_only_with_masked_password():
    result = _run({"DATABASE_URL": "postgresql://reader:s3cret@localhost:6543/transit"})
    assert result.returncode == 0, result.stderr
    assert "reader:***@host.docker.internal:6543/transit" in result.stdout
    assert "default_transaction_read_only%3Don" in result.stdout
    assert "s3cret" not in result.stdout


def test_existing_query_options_are_extended_not_replaced():
    result = _run({"DATABASE_URL": "postgresql://u:p@db.example:5432/transit?sslmode=require"})
    assert result.returncode == 0, result.stderr
    assert "transit?sslmode=require&options=-c%20default_transaction_read_only%3Don" in result.stdout
    assert "db.example" in result.stdout


def test_missing_database_url_fails_instead_of_guessing_a_port():
    with tempfile.TemporaryDirectory() as tmp:
        # A script copy in an empty tree has no `.env` beside it to fall back on.
        tree = Path(tmp) / "tools" / "geosql"
        tree.mkdir(parents=True)
        copy = tree / "bootstrap.sh"
        copy.write_text(SCRIPT.read_text())
        env = {k: v for k, v in os.environ.items() if k != "DATABASE_URL"}
        result = subprocess.run(["bash", str(copy)], env=env, capture_output=True, text=True, timeout=30)
    assert result.returncode != 0
    assert "DATABASE_URL is not set" in result.stdout


def test_readiness_wait_is_bounded():
    result = _run(
        {"DATABASE_URL": "postgresql://u:p@localhost:6543/transit", "DEKART_WAIT_SECONDS": "2"}, curl_ok=False
    )
    assert result.returncode != 0
    assert "did not answer" in result.stdout
