"""scripts/dev_upgrade.py: the one path by which an agent writes to the dev
stores refuses everything that could carry unmerged code or schema there."""

from __future__ import annotations

import contextlib
import importlib.util
import pathlib
import subprocess

import pytest

_SPEC = importlib.util.spec_from_file_location(
    "dev_upgrade", pathlib.Path(__file__).resolve().parents[2] / "scripts" / "dev_upgrade.py"
)
dev_upgrade = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(dev_upgrade)


def _git(cwd: pathlib.Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(cwd), *args], check=True, capture_output=True)


@pytest.fixture
def checkout(tmp_path: pathlib.Path) -> pathlib.Path:
    """A primary checkout at origin/main, cloned from a bare origin."""
    origin = tmp_path / "origin.git"
    subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(origin)], check=True)
    repo = tmp_path / "repo"
    subprocess.run(["git", "clone", "-q", str(origin), str(repo)], check=True, capture_output=True)
    _git(repo, "config", "user.email", "t@example.com")
    _git(repo, "config", "user.name", "t")
    _git(repo, "config", "commit.gpgsign", "false")
    _git(repo, "checkout", "-q", "-b", "main")
    (repo / "a.txt").write_text("a\n")
    _git(repo, "add", "a.txt")
    _git(repo, "commit", "-q", "-m", "a")
    _git(repo, "push", "-q", "origin", "main")
    return repo


def test_the_primary_checkout_at_origin_main_passes(checkout):
    dev_upgrade.check_checkout(checkout)


def test_a_linked_worktree_is_refused(checkout, tmp_path):
    linked = tmp_path / "linked"
    _git(checkout, "worktree", "add", "-q", "--detach", str(linked), "main")
    with pytest.raises(dev_upgrade.Refusal, match="linked worktree"):
        dev_upgrade.check_checkout(linked)


def test_an_uncommitted_change_is_refused(checkout):
    (checkout / "a.txt").write_text("changed\n")
    with pytest.raises(dev_upgrade.Refusal, match="uncommitted"):
        dev_upgrade.check_checkout(checkout)


def test_an_untracked_file_no_step_reads_does_not_block(checkout):
    (checkout / "notes.txt").write_text("scratch\n")
    (checkout / "r2_readonly.txt").write_text("not read\n")
    (checkout / "pipeline").mkdir()
    (checkout / "pipeline" / "notes.md").write_text("scratch\n")
    dev_upgrade.check_checkout(checkout)


@pytest.mark.parametrize(
    "path",
    ["db/migrations/0099_unmerged.up.sql", "db/notes.txt", "pipeline/unmerged.py", "local.py", "q.sql", "GNUmakefile"],
)
def test_an_untracked_file_a_step_could_pick_up_is_refused(checkout, path):
    (checkout / path).parent.mkdir(parents=True, exist_ok=True)
    (checkout / path).write_text("x\n")
    with pytest.raises(dev_upgrade.Refusal, match="untracked") as refused:
        dev_upgrade.check_checkout(checkout)
    assert path in str(refused.value)


def test_an_ignored_file_does_not_block(checkout):
    (checkout / ".gitignore").write_text(".worktrees/\n")
    _git(checkout, "add", ".gitignore")
    _git(checkout, "commit", "-q", "-m", "ignore")
    _git(checkout, "push", "-q", "origin", "main")
    (checkout / ".worktrees" / "x" / "db").mkdir(parents=True)
    (checkout / ".worktrees" / "x" / "db" / "a.py").write_text("x\n")
    dev_upgrade.check_checkout(checkout)


def test_a_failing_fetch_is_a_refusal(checkout, tmp_path):
    _git(checkout, "remote", "set-url", "origin", str(tmp_path / "missing.git"))
    with pytest.raises(dev_upgrade.Refusal, match="git fetch failed") as refused:
        dev_upgrade.check_checkout(checkout)
    assert "missing.git" in str(refused.value)


def test_a_git_failure_names_no_credentials(monkeypatch):
    def fail(*args, **kwargs):
        raise subprocess.CalledProcessError(
            128, args[0], stderr="fatal: unable to access 'https://x-access-token:s3cret@github.com/o/r.git/'\n"
        )

    monkeypatch.setattr(dev_upgrade.subprocess, "run", fail)
    with pytest.raises(dev_upgrade.Refusal) as refused:
        dev_upgrade._git("fetch", "-q", "origin", "main")
    assert "s3cret" not in str(refused.value)
    assert "github.com/o/r.git" in str(refused.value)


def test_a_git_error_exits_with_a_refusal_not_a_traceback(monkeypatch, capsys, tmp_path):
    check_checkout = dev_upgrade.check_checkout
    monkeypatch.setattr(dev_upgrade, "check_checkout", lambda: check_checkout(tmp_path))
    assert dev_upgrade.main(["--check"]) == 3
    assert "refused: git rev-parse failed" in capsys.readouterr().err


def test_a_head_ahead_of_origin_main_is_refused(checkout):
    (checkout / "b.txt").write_text("b\n")
    _git(checkout, "add", "b.txt")
    _git(checkout, "commit", "-q", "-m", "unmerged")
    with pytest.raises(dev_upgrade.Refusal, match="is not origin/main"):
        dev_upgrade.check_checkout(checkout)


def test_a_head_behind_origin_main_is_refused(checkout, tmp_path):
    other = tmp_path / "other"
    subprocess.run(["git", "clone", "-q", str(tmp_path / "origin.git"), str(other)], check=True, capture_output=True)
    _git(other, "config", "user.email", "t@example.com")
    _git(other, "config", "user.name", "t")
    _git(other, "config", "commit.gpgsign", "false")
    (other / "c.txt").write_text("c\n")
    _git(other, "add", "c.txt")
    _git(other, "commit", "-q", "-m", "merged elsewhere")
    _git(other, "push", "-q", "origin", "main")
    with pytest.raises(dev_upgrade.Refusal, match="is not origin/main"):
        dev_upgrade.check_checkout(checkout)


def test_a_second_run_waits_for_the_lock(checkout):
    with dev_upgrade.upgrade_lock(checkout):
        with pytest.raises(dev_upgrade.Refusal, match="another dev upgrade"):
            with dev_upgrade.upgrade_lock(checkout):
                pass
    with dev_upgrade.upgrade_lock(checkout):
        pass


def test_only_pending_up_migrations_that_lose_data_are_reported(tmp_path):
    (tmp_path / "0001_add.up.sql").write_text("ALTER TABLE t ADD COLUMN c INT;")
    (tmp_path / "0002_drop.up.sql").write_text("-- note\nalter table users\n  drop c;")
    (tmp_path / "0003_drop_applied.up.sql").write_text("DROP TABLE users;")
    (tmp_path / "0004_backfill.up.sql").write_text("UPDATE users SET email = lower(email);")
    (tmp_path / "0005_rebuildable.up.sql").write_text(
        "DROP TABLE IF EXISTS agg_route_dow; DELETE FROM agg_route_daily;"
    )
    found = dev_upgrade.destructive_pending(["0001", "0002", "0004", "0005"], tmp_path)
    assert found == {"0002_drop": ["users"]}


def test_the_database_url_is_read_as_the_makefile_reads_it(tmp_path):
    (tmp_path / ".env").write_text("# comment\nOTHER=1\nexport DATABASE_URL='postgresql://u@h:5543/d'\n")
    assert dev_upgrade.dev_database_url(tmp_path) == "postgresql://u@h:5543/d"


def test_the_last_database_url_wins_as_in_make(tmp_path):
    (tmp_path / ".env").write_text(
        "DATABASE_URL=postgresql://u@h:6001/first\nDATABASE_URL=postgresql://u@h:6002/last\n"
    )
    assert dev_upgrade.dev_database_url(tmp_path) == "postgresql://u@h:6002/last"


def test_an_emptied_database_url_is_refused(tmp_path):
    (tmp_path / ".env").write_text("DATABASE_URL=postgresql://u@h:6001/d\nDATABASE_URL=\n")
    with pytest.raises(dev_upgrade.Refusal, match="no DATABASE_URL"):
        dev_upgrade.dev_database_url(tmp_path)


def test_no_database_url_is_refused(tmp_path):
    (tmp_path / ".env").write_text("OTHER=1\n")
    with pytest.raises(dev_upgrade.Refusal, match="no DATABASE_URL"):
        dev_upgrade.dev_database_url(tmp_path)


def test_a_pending_migration_that_changes_rows_stops_a_real_run_before_any_step(monkeypatch, capsys):
    monkeypatch.setattr(dev_upgrade, "check_checkout", lambda: None)
    monkeypatch.setattr(dev_upgrade, "dev_database_url", lambda: "postgresql://x")
    monkeypatch.setattr(dev_upgrade, "pending_versions", lambda url: ["0099"])
    monkeypatch.setattr(dev_upgrade, "destructive_pending", lambda pending: {"0099_drop": ["users"]})
    ran = []
    monkeypatch.setattr(dev_upgrade, "_run", lambda step, log: ran.append(step))
    assert dev_upgrade.main([]) == 3
    assert ran == []
    assert "0099_drop" in capsys.readouterr().err


def test_the_owner_naming_the_version_lets_it_run(monkeypatch, tmp_path):
    monkeypatch.setattr(dev_upgrade, "check_checkout", lambda: None)
    monkeypatch.setattr(dev_upgrade, "dev_database_url", lambda: "postgresql://x")
    monkeypatch.setattr(dev_upgrade, "pending_versions", lambda url: ["0099"])
    monkeypatch.setattr(dev_upgrade, "destructive_pending", lambda pending: {"0099_drop": ["users"]})
    monkeypatch.setattr(dev_upgrade, "upgrade_lock", lambda: contextlib.nullcontext())
    monkeypatch.setattr(dev_upgrade, "LOG_DIR", tmp_path)
    ran = []
    monkeypatch.setattr(dev_upgrade, "_run", lambda step, log: ran.append(step))
    assert dev_upgrade.main(["--allow-destructive-up", "0099"]) == 0
    assert ran == list(dev_upgrade.STEPS)


def test_check_writes_nothing(monkeypatch, capsys):
    monkeypatch.setattr(dev_upgrade, "check_checkout", lambda: None)
    monkeypatch.setattr(dev_upgrade, "dev_database_url", lambda: "postgresql://x")
    monkeypatch.setattr(dev_upgrade, "pending_versions", lambda url: [])
    ran = []
    monkeypatch.setattr(dev_upgrade, "_run", lambda step, log: ran.append(step))
    assert dev_upgrade.main(["--check"]) == 0
    assert ran == []
    assert "plan: migrate -> ch-bootstrap -> analyze-all -> check-aggs" in capsys.readouterr().out


def test_a_resumed_run_starts_at_its_step(monkeypatch, tmp_path):
    monkeypatch.setattr(dev_upgrade, "check_checkout", lambda: None)
    monkeypatch.setattr(dev_upgrade, "dev_database_url", lambda: "postgresql://x")
    monkeypatch.setattr(dev_upgrade, "pending_versions", lambda url: [])
    monkeypatch.setattr(dev_upgrade, "upgrade_lock", lambda: contextlib.nullcontext())
    monkeypatch.setattr(dev_upgrade, "LOG_DIR", tmp_path)
    ran = []
    monkeypatch.setattr(dev_upgrade, "_run", lambda step, log: ran.append(step))
    assert dev_upgrade.main(["--start", "3"]) == 0
    assert ran == ["analyze-all", "check-aggs"]


@pytest.mark.parametrize("destructive", [{}, {"0099_drop": ["users"]}])
def test_a_resumed_run_with_pending_migrations_is_refused(monkeypatch, capsys, destructive):
    monkeypatch.setattr(dev_upgrade, "check_checkout", lambda: None)
    monkeypatch.setattr(dev_upgrade, "dev_database_url", lambda: "postgresql://x")
    monkeypatch.setattr(dev_upgrade, "pending_versions", lambda url: ["0099"])
    monkeypatch.setattr(dev_upgrade, "destructive_pending", lambda pending: destructive)
    ran = []
    monkeypatch.setattr(dev_upgrade, "_run", lambda step, log: ran.append(step))
    assert dev_upgrade.main(["--start", "2", "--allow-destructive-up", "0099"]) == 3
    assert ran == []
    assert "pending migrations; start from step 1" in capsys.readouterr().err


def test_check_reports_that_a_resumed_run_with_pending_migrations_refuses(monkeypatch, capsys):
    monkeypatch.setattr(dev_upgrade, "check_checkout", lambda: None)
    monkeypatch.setattr(dev_upgrade, "dev_database_url", lambda: "postgresql://x")
    monkeypatch.setattr(dev_upgrade, "pending_versions", lambda url: ["0099"])
    monkeypatch.setattr(dev_upgrade, "destructive_pending", lambda pending: {})
    ran = []
    monkeypatch.setattr(dev_upgrade, "_run", lambda step, log: ran.append(step))
    assert dev_upgrade.main(["--check", "--start", "2"]) == 0
    assert ran == []
    assert "a real run refuses: pending migrations; start from step 1" in capsys.readouterr().out


def test_a_refused_checkout_writes_nothing(monkeypatch, capsys):
    def refuse():
        raise dev_upgrade.Refusal("x is a linked worktree; run from the primary checkout")

    monkeypatch.setattr(dev_upgrade, "check_checkout", refuse)
    ran = []
    monkeypatch.setattr(dev_upgrade, "_run", lambda step, log: ran.append(step))
    assert dev_upgrade.main([]) == 3
    assert ran == []
    assert "linked worktree" in capsys.readouterr().err


def test_a_step_takes_its_stores_from_the_checkouts_env_alone():
    # A shell left pointed at the throwaway pair must not steer a step: .env
    # names every store the Makefile exports, and a key it leaves unset falls
    # back to the code's default rather than the caller's value.
    env = dev_upgrade.step_env(
        {"PATH": "/bin", "HOME": "/h", "DATABASE_URL": "postgresql://x@localhost:5544/t", "CLICKHOUSE_PORT": "8124"}
    )
    assert env == {"PATH": "/bin", "HOME": "/h"}


def test_a_step_runs_in_the_stripped_environment(monkeypatch, tmp_path):
    seen = {}

    class _Proc:
        returncode = 0
        stdout: list[str] = []

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    def popen(command, **kwargs):
        seen.update(kwargs)
        return _Proc()

    monkeypatch.setenv("CLICKHOUSE_PORT", "8124")
    monkeypatch.setattr(dev_upgrade.subprocess, "Popen", popen)
    with (tmp_path / "log").open("w") as log:
        dev_upgrade._run("check-aggs", log)
    assert "CLICKHOUSE_PORT" not in seen["env"]
