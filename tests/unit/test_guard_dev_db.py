"""Behavioural tests for the dev-DB write guard (`.claude/hooks/guard-dev-db.sh`).

The hook is the last automated thing standing between an agent and a write
against the real dev database, and it is edited by hand whenever container
names or ports move. Every case below is driven through the shell entry point
settings.json actually registers, so the wrapper and the Python body are both
covered rather than only the importable half.

Each blocked case pairs a dev-DB target with a mutating statement; each allowed
case keeps one of the two away, since it takes both to justify blocking.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

HOOK = Path(__file__).resolve().parents[2] / ".claude" / "hooks" / "guard-dev-db.sh"

BLOCKED = [
    pytest.param('psql postgresql://transit:transit@localhost:5433/transit -c "DROP TABLE updates"', id="url"),
    pytest.param('psql -h localhost -p 5433 -U transit -c "DELETE FROM updates"', id="separated-host-port"),
    pytest.param('psql -h 127.0.0.1 -p 5433 -U transit -c "DELETE FROM updates"', id="loopback-ip"),
    pytest.param('PGPORT=5433 PGHOST=localhost psql -U transit -c "DELETE FROM updates"', id="env-port"),
    pytest.param('docker compose exec db psql -U transit -c "TRUNCATE updates"', id="compose-exec"),
    pytest.param('docker compose run --rm db psql -h db -U transit -c "DROP TABLE updates"', id="compose-run"),
    pytest.param(
        'docker compose --project-name transit exec db psql -U transit -c "DROP TABLE updates"',
        id="compose-global-flag",
    ),
    pytest.param('docker exec transit-app-db-1 psql -U transit -c "DROP TABLE updates"', id="derived-container"),
    pytest.param('docker exec transit-pg psql -U transit -c "DROP TABLE updates"', id="legacy-container"),
    pytest.param("make migrate-down CONFIRM=1", id="migrate-down-inherits-dev-default"),
]

ALLOWED = [
    pytest.param(
        'psql postgresql://transit:transit@localhost:5433/transit -c "SELECT count(*) FROM updates"', id="dev-read"
    ),
    pytest.param("docker compose exec db psql -U transit -c 'EXPLAIN SELECT 1'", id="dev-explain"),
    pytest.param('psql postgresql://transit:transit@localhost:5544/transit_test -c "DROP TABLE updates"', id="test-db"),
    pytest.param('psql -h localhost -p 5544 -U transit -c "DELETE FROM updates"', id="test-db-separated"),
    pytest.param("ls -la", id="unrelated"),
    pytest.param('echo "DROP TABLE updates"', id="write-without-target"),
    pytest.param(
        "DATABASE_URL=postgresql://transit:transit@localhost:5544/transit_test make migrate-down CONFIRM=1",
        id="migrate-down-pointed-at-test-db",
    ),
]


def _run(command: str) -> int:
    payload = json.dumps({"tool_input": {"command": command}})
    return subprocess.run([str(HOOK)], input=payload, text=True, capture_output=True).returncode


@pytest.mark.parametrize("command", BLOCKED)
def test_write_against_dev_db_is_blocked(command):
    assert _run(command) == 2, f"guard let a dev-DB write through: {command}"


@pytest.mark.parametrize("command", ALLOWED)
def test_safe_command_is_allowed(command):
    assert _run(command) == 0, f"guard blocked a safe command: {command}"


def test_malformed_input_does_not_block():
    """A payload the hook can't parse must not wedge every Bash call."""
    assert subprocess.run([str(HOOK)], input="not json", text=True, capture_output=True).returncode == 0
