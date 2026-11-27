"""scripts/standalone_restore.sh, driven through a shimmed `docker`.

The shim stands in for the stack: it answers the two table counts with
whatever the test sets, records every call, and on request fails the
ClickHouse RESTORE or sends the script SIGTERM during it.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import textwrap
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "standalone_restore.sh"


@pytest.fixture
def stack(tmp_path):
    checkout = tmp_path / "checkout"
    (checkout / "scripts").mkdir(parents=True)
    shutil.copy(SCRIPT, checkout / "scripts" / SCRIPT.name)
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    calls = tmp_path / "calls"
    docker = bin_dir / "docker"
    docker.write_text(
        textwrap.dedent(
            f"""\
            #!/usr/bin/env bash
            echo "$*" >> "{calls}"
            case "$*" in
              *" psql "*) echo "${{PG_TABLES:-0}}" ;;
              *"system.tables"*) echo "${{CH_TABLES:-0}}" ;;
              *"RESTORE DATABASE"*) [ -n "$TERM_CH" ] && kill -TERM "$PPID"; [ -n "$FAIL_CH" ] && exit 1 ;;
              *" sh -c "*) cat >/dev/null ;;
            esac
            exit 0
            """
        )
    )
    docker.chmod(0o755)
    backup = tmp_path / "20260101T031500Z"
    backup.mkdir()
    (backup / "postgres.dump").write_bytes(b"PGDMP")
    (backup / "clickhouse.zip").write_bytes(b"PK-zip")

    def run(**env: str) -> subprocess.CompletedProcess:
        full = {"PATH": f"{bin_dir}:{os.environ['PATH']}", "HOME": str(tmp_path), **env}
        return subprocess.run(
            ["bash", str(checkout / "scripts" / SCRIPT.name), str(backup)], env=full, capture_output=True, text=True
        )

    run.backup = backup
    run.calls = lambda: calls.read_text().splitlines() if calls.exists() else []
    return run


def _index(calls: list[str], needle: str) -> int:
    return next(i for i, call in enumerate(calls) if needle in call)


def test_a_backup_restores_postgres_then_clickhouse_and_removes_the_copy(stack):
    result = stack()
    assert result.returncode == 0, result.stderr
    calls = stack.calls()
    restore = calls[_index(calls, "RESTORE DATABASE")]
    assert "--receive_timeout=86400" in restore
    copied = restore.split("File('")[1].split("'")[0]
    assert _index(calls, "pg_restore") < _index(calls, "RESTORE DATABASE")
    assert any(" rm -f " in call and call.endswith(copied) for call in calls[_index(calls, "RESTORE DATABASE") :])


@pytest.mark.parametrize("tables", [{"PG_TABLES": "3"}, {"CH_TABLES": "1"}])
def test_a_database_holding_a_table_is_refused_before_any_restore(stack, tables):
    result = stack(**tables)
    assert result.returncode == 1
    assert "refused" in result.stderr
    assert not any("pg_restore" in call or "RESTORE DATABASE" in call for call in stack.calls())


@pytest.mark.parametrize("cause", ["FAIL_CH", "TERM_CH"])
def test_a_clickhouse_restore_that_does_not_finish_says_how_to_start_again(stack, cause):
    result = stack(**{cause: "1"})
    assert result.returncode != 0
    assert "remove both database volumes" in result.stderr
    assert any(" rm -f /var/lib/clickhouse/backups/" in call for call in stack.calls())


@pytest.mark.parametrize("missing", ["postgres.dump", "clickhouse.zip"])
def test_a_backup_missing_a_file_is_refused_before_anything_runs(stack, missing):
    (stack.backup / missing).unlink()
    result = stack()
    assert result.returncode == 2
    assert stack.calls() == []
