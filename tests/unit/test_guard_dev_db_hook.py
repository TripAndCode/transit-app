"""Regression coverage for guard-dev-db.sh's destructive-command patterns
against the dev Postgres (:5433, transit-pg) and dev ClickHouse
(transit-ch, :8123) stores.

Runs the actual hook script via `subprocess`, feeding it the same JSON-on-
stdin payload Claude Code's PreToolUse(Bash) hook contract uses, and asserts
block (exit 2) vs allow (exit 0). The sampled commands below are only ever
JSON string payloads fed to the hook's own stdin parser -- none of them are
ever executed as shell commands by this test.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
HOOK_PATH = ROOT / ".claude" / "hooks" / "guard-dev-db.sh"


def _run_hook(command: str) -> subprocess.CompletedProcess[str]:
    payload = json.dumps({"tool_input": {"command": command}})
    return subprocess.run(
        ["bash", str(HOOK_PATH)],
        input=payload,
        capture_output=True,
        text=True,
        timeout=10,
    )


BLOCKED_POSTGRES_DEV_DB = [
    "psql postgresql://transit:transit@localhost:5433/transit -c 'INSERT INTO foo VALUES (1)'",
    "dropdb -h transit-pg transit",
    "createdb -h transit-pg transit_extra",
    "pg_restore -h transit-pg -d transit backup.dump",
    "psql -h transit-pg -d transit -f migration.sql",
    r"psql postgresql://transit:transit@localhost:5433/transit -c \copy stops from '/tmp/stops.csv' csv",
    "psql postgresql://transit:transit@localhost:5433/transit -c 'REINDEX TABLE stops'",
    "psql postgresql://transit:transit@localhost:5433/transit -c 'VACUUM FULL stops'",
    "psql postgresql://transit:transit@localhost:5433/transit -c 'TRUNCATE stops'",
]

ALLOWED_POSTGRES = [
    "psql postgresql://transit:transit@localhost:5433/transit -c 'SELECT * FROM stops LIMIT 1'",
    r"psql postgresql://transit:transit@localhost:5433/transit -c \copy stops to '/tmp/stops.csv' csv",
    "psql postgresql://transit:transit@localhost:5544/transit_test -c 'INSERT INTO foo VALUES (1)'",
    "psql postgresql://transit:transit@localhost:5433/transit -c 'VACUUM stops'",
]

BLOCKED_CLICKHOUSE_DEV = [
    "clickhouse-client --host transit-ch --query 'INSERT INTO updates VALUES (1)'",
    "clickhouse-client --host transit-ch --query 'TRUNCATE TABLE updates'",
    "curl -s 'http://localhost:8123/' --data-binary 'ALTER TABLE updates DELETE WHERE 1=1'",
    "curl -s 'http://transit-ch:8123/' --data-binary 'DROP TABLE updates'",
]

ALLOWED_CLICKHOUSE = [
    "clickhouse-client --host transit-ch --query 'SELECT count() FROM updates'",
    "curl -s 'http://localhost:8124/' --data-binary 'INSERT INTO updates VALUES (1)'",
    "grep -R 'transit-ch' docs/ | grep INSERT",
]


@pytest.mark.parametrize("command", BLOCKED_POSTGRES_DEV_DB)
def test_blocks_postgres_dev_db_writes(command: str) -> None:
    result = _run_hook(command)
    assert result.returncode == 2, f"expected block for {command!r}: {result.stdout}{result.stderr}"
    assert "BLOCKED" in result.stderr


@pytest.mark.parametrize("command", ALLOWED_POSTGRES)
def test_allows_postgres_reads_and_test_db(command: str) -> None:
    result = _run_hook(command)
    assert result.returncode == 0, f"expected allow for {command!r}: {result.stdout}{result.stderr}"


@pytest.mark.parametrize("command", BLOCKED_CLICKHOUSE_DEV)
def test_blocks_clickhouse_dev_writes(command: str) -> None:
    result = _run_hook(command)
    assert result.returncode == 2, f"expected block for {command!r}: {result.stdout}{result.stderr}"
    assert "BLOCKED" in result.stderr


@pytest.mark.parametrize("command", ALLOWED_CLICKHOUSE)
def test_allows_clickhouse_reads_and_test_ch(command: str) -> None:
    result = _run_hook(command)
    assert result.returncode == 0, f"expected allow for {command!r}: {result.stdout}{result.stderr}"


def test_fails_open_on_malformed_json() -> None:
    result = subprocess.run(
        ["bash", str(HOOK_PATH)],
        input="not json",
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert result.returncode == 0
