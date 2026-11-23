"""scripts/standalone_backup.sh, driven through shimmed `docker` and `curl`.

The shims stand in for the stack: `docker compose exec` answers pg_dump and the
ClickHouse calls with fixed bytes, and `curl` records where each ping went. A
copy of the script runs from a scratch checkout, so `.env` there is the
test's own.
"""

from __future__ import annotations

import os
import shutil
import stat
import subprocess
import textwrap
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "standalone_backup.sh"


def _executable(path: Path, body: str) -> None:
    path.write_text(textwrap.dedent(body))
    path.chmod(0o755)


@pytest.fixture
def stack(tmp_path):
    checkout = tmp_path / "checkout"
    (checkout / "scripts").mkdir(parents=True)
    shutil.copy(SCRIPT, checkout / "scripts" / SCRIPT.name)
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    calls = tmp_path / "calls"
    pings = tmp_path / "pings"
    _executable(
        bin_dir / "docker",
        f"""\
        #!/usr/bin/env bash
        echo "$*" >> "{calls}"
        case "$*" in
          *"pg_dump"*) [ -n "$FAIL_PG" ] && exit 1; printf 'PGDMP' ;;
          *" cat /var/lib/clickhouse/backups/"*) printf 'PK-zip' ;;
          *" run "*) [ -n "$FAIL_UPLOAD" ] && exit 1 ;;
        esac
        exit 0
        """,
    )
    _executable(
        bin_dir / "curl",
        f"""\
        #!/usr/bin/env bash
        echo "${{@: -1}}" >> "{pings}"
        """,
    )
    backups = tmp_path / "backups"
    backups.mkdir()

    def run(env_file: str = "", **env: str) -> subprocess.CompletedProcess:
        (checkout / ".env").write_text(env_file)
        full = {
            "PATH": f"{bin_dir}:{os.environ['PATH']}",
            "HOME": str(tmp_path),
            "BACKUP_DIR": str(backups),
            **env,
        }
        return subprocess.run(
            ["bash", str(checkout / "scripts" / SCRIPT.name)], env=full, capture_output=True, text=True
        )

    def read(path: Path) -> list[str]:
        return path.read_text().splitlines() if path.exists() else []

    run.backups = backups
    run.calls = lambda: read(calls)
    run.pings = lambda: read(pings)
    return run


def _stamps(backups: Path) -> list[str]:
    return sorted(p.name for p in backups.iterdir())


def test_a_backup_writes_both_files_and_pings_the_check(stack):
    result = stack(env_file="BACKUP_PING_URL=https://hc.example/ping/key\n")
    assert result.returncode == 0, result.stderr
    [stamp] = _stamps(stack.backups)
    assert (stack.backups / stamp / "postgres.dump").read_bytes() == b"PGDMP"
    assert (stack.backups / stamp / "clickhouse.zip").read_bytes() == b"PK-zip"
    modes = {p.name: stat.S_IMODE(p.stat().st_mode) for p in (stack.backups / stamp).iterdir()}
    assert modes == {"postgres.dump": 0o600, "clickhouse.zip": 0o600}
    assert stat.S_IMODE((stack.backups / stamp).stat().st_mode) == 0o700
    assert stack.pings() == ["https://hc.example/ping/key"]
    assert not any(" run " in call for call in stack.calls())
    assert "hc.example" not in result.stdout + result.stderr


def test_a_failed_dump_keeps_nothing_and_pings_fail_with_the_query_kept(stack):
    result = stack(BACKUP_PING_URL="https://hc.example/ping/key/job?create=1", FAIL_PG="1")
    assert result.returncode != 0
    assert _stamps(stack.backups) == []
    assert stack.pings() == ["https://hc.example/ping/key/job/fail?create=1"]
    assert "hc.example" not in result.stdout + result.stderr


def test_an_upload_runs_only_with_a_bucket_and_a_failed_one_keeps_the_local_copy(stack):
    result = stack(env_file="BACKUP_STORE_BUCKET=b\nBACKUP_PING_URL=https://hc.example/p\n", FAIL_UPLOAD="1")
    assert result.returncode != 0
    assert len(_stamps(stack.backups)) == 1
    [upload] = [call for call in stack.calls() if " run " in call]
    assert "scripts/upload_backup.py" in upload
    assert f" -u {os.getuid()}:{os.getgid()} " in upload
    assert stack.pings() == ["https://hc.example/p/fail"]


def test_only_the_newest_backups_are_kept_and_nothing_else_is_touched(stack):
    for name in ("20250101T000000Z", "20250102T000000Z", "20250103T000000Z", "keep-me"):
        (stack.backups / name).mkdir()
    result = stack(BACKUP_KEEP="2")
    assert result.returncode == 0, result.stderr
    names = _stamps(stack.backups)
    assert "keep-me" in names
    assert "20250103T000000Z" in names
    assert "20250101T000000Z" not in names
    assert "20250102T000000Z" not in names
    assert len(names) == 3


def test_the_environment_wins_over_env_file(stack):
    for name in ("20250101T000000Z", "20250102T000000Z"):
        (stack.backups / name).mkdir()
    result = stack(env_file="BACKUP_KEEP=1\n", BACKUP_KEEP="5")
    assert result.returncode == 0, result.stderr
    assert len(_stamps(stack.backups)) == 3


def test_a_tilde_in_env_file_is_the_home_directory(stack, tmp_path):
    result = stack(env_file="BACKUP_DIR=~/elsewhere\n", BACKUP_DIR="")
    assert result.returncode == 0, result.stderr
    assert len(list((tmp_path / "elsewhere").iterdir())) == 1


@pytest.mark.parametrize("backup_dir", ["relative/dir", "$HOME/x"])
def test_a_backup_dir_that_is_not_absolute_is_refused(stack, backup_dir):
    result = stack(env_file=f"BACKUP_DIR={backup_dir}\n", BACKUP_DIR="")
    assert result.returncode == 2
    assert stack.calls() == []


@pytest.mark.parametrize("keep", ["0", "x", "-1"])
def test_a_bad_keep_count_is_refused_before_anything_runs(stack, keep):
    result = stack(BACKUP_KEEP=keep)
    assert result.returncode == 2
    assert stack.calls() == []
