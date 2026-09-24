"""Behavioural tests for the dev-store write guard (`.claude/hooks/guard-dev-db.sh`).

The hook is the last automated thing standing between an agent and a write
against the real dev Postgres (:5433) or dev ClickHouse (:8123), and it is
edited by hand whenever container names or ports move. Every case below is
driven through the shell entry point settings.json actually registers, so the
wrapper and the Python body are both covered rather than only the importable
half.

Each blocked case pairs a dev target with a mutating statement; each allowed
case keeps one of the two away, since it takes both to justify blocking. The
commands are only ever JSON string payloads fed to the hook's stdin parser —
nothing here executes them.
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
    # The dev dataset is not always on the port compose.yml declares -- the
    # live container publishes :5543 -- and the guard has to cover the port
    # the data is actually reachable on, not the documented one.
    pytest.param(
        'psql postgresql://transit:transit@localhost:5543/transit -c "DROP TABLE updates"', id="alt-dev-port-url"
    ),
    pytest.param('psql -h localhost -p 5543 -U transit -c "DELETE FROM updates"', id="alt-dev-port-separated"),
    pytest.param('docker compose exec db psql -U transit -c "TRUNCATE updates"', id="compose-exec"),
    pytest.param('docker compose run --rm db psql -h db -U transit -c "DROP TABLE updates"', id="compose-run"),
    pytest.param(
        'docker compose --project-name transit exec db psql -U transit -c "DROP TABLE updates"',
        id="compose-global-flag",
    ),
    pytest.param('docker exec transit-app-db-1 psql -U transit -c "DROP TABLE updates"', id="derived-container"),
    pytest.param('docker exec transit-pg psql -U transit -c "DROP TABLE updates"', id="legacy-container"),
    pytest.param("make migrate-down CONFIRM=1", id="migrate-down-inherits-dev-default"),
    # Postgres CLIs that mutate without ever spelling a SQL keyword.
    pytest.param("dropdb -h transit-pg transit", id="dropdb"),
    pytest.param("createdb -h transit-pg transit_extra", id="createdb"),
    pytest.param("pg_restore -h transit-pg -d transit backup.dump", id="pg_restore"),
    # The statements live in a file this hook cannot read; the flag is the
    # only evidence, so it has to be enough.
    pytest.param("psql -h transit-pg -d transit -f migration.sql", id="psql-script-file"),
    pytest.param(
        r"psql postgresql://transit:transit@localhost:5433/transit -c \copy stops from '/tmp/stops.csv' csv",
        id="copy-from-loads-data-in",
    ),
    pytest.param('psql postgresql://transit:transit@localhost:5433/transit -c "REINDEX TABLE stops"', id="reindex"),
    pytest.param('psql postgresql://transit:transit@localhost:5433/transit -c "VACUUM FULL stops"', id="vacuum-full"),
    # Dev ClickHouse, reached by its pinned container name, its port, or the
    # compose service.
    pytest.param("clickhouse-client --host transit-ch --query 'INSERT INTO updates VALUES (1)'", id="ch-client-insert"),
    pytest.param("clickhouse-client --host transit-ch --query 'TRUNCATE TABLE updates'", id="ch-client-truncate"),
    pytest.param(
        "curl -s 'http://localhost:8123/' --data-binary 'ALTER TABLE updates DELETE WHERE 1=1'",
        id="ch-http-port",
    ),
    pytest.param("curl -s 'http://transit-ch:8123/' --data-binary 'DROP TABLE updates'", id="ch-http-container"),
    pytest.param("docker compose exec clickhouse clickhouse-client -q 'DROP TABLE updates'", id="ch-compose-exec"),
    # Naming a dev store next to a write keyword is enough on its own, even
    # when the command only searches text. Blocking is the cheap direction:
    # this costs a rephrase, the alternative costs the dataset.
    pytest.param("grep -R 'transit-ch' docs/ | grep INSERT", id="mention-without-intent-still-blocks"),
]

ALLOWED = [
    pytest.param(
        'psql postgresql://transit:transit@localhost:5433/transit -c "SELECT count(*) FROM updates"', id="dev-read"
    ),
    pytest.param("docker compose exec db psql -U transit -c 'EXPLAIN SELECT 1'", id="dev-explain"),
    pytest.param(
        'psql postgresql://transit:transit@localhost:5543/transit -c "SELECT count(*) FROM agencies"',
        id="alt-dev-port-read",
    ),
    pytest.param('psql postgresql://transit:transit@localhost:5544/transit_test -c "DROP TABLE updates"', id="test-db"),
    pytest.param('psql -h localhost -p 5544 -U transit -c "DELETE FROM updates"', id="test-db-separated"),
    pytest.param("ls -la", id="unrelated"),
    pytest.param('echo "DROP TABLE updates"', id="write-without-target"),
    pytest.param(
        "DATABASE_URL=postgresql://transit:transit@localhost:5544/transit_test make migrate-down CONFIRM=1",
        id="migrate-down-pointed-at-test-db",
    ),
    pytest.param(
        r"psql postgresql://transit:transit@localhost:5433/transit -c \copy stops to '/tmp/stops.csv' csv",
        id="copy-to-reads-data-out",
    ),
    pytest.param(
        'psql postgresql://transit:transit@localhost:5433/transit -c "VACUUM stops"',
        id="plain-vacuum-locks-nothing",
    ),
    pytest.param("clickhouse-client --host transit-ch --query 'SELECT count() FROM updates'", id="ch-read"),
    pytest.param("curl -s 'http://localhost:8124/' --data-binary 'INSERT INTO updates VALUES (1)'", id="test-ch"),
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
