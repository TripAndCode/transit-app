"""scripts/dev_upgrade.py: the one path by which an agent writes to the dev
stores refuses everything that could carry unmerged code or schema there."""

from __future__ import annotations

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


def test_an_untracked_file_alone_does_not_block(checkout):
    (checkout / "notes.txt").write_text("scratch\n")
    dev_upgrade.check_checkout(checkout)


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


@pytest.mark.parametrize(
    "sql",
    [
        "ALTER TABLE t DROP COLUMN c;",
        "ALTER TABLE t DROP COLUMN IF EXISTS c;",
        "ALTER TABLE t DROP c;",
        "DROP TABLE IF EXISTS t;",
        "DELETE FROM t WHERE x = 1;",
        "TRUNCATE t;",
        "UPDATE t SET x = 1;",
        "INSERT INTO t VALUES (1) ON CONFLICT (id) DO UPDATE SET x = 1;",
        "drop   table\n  t;",
    ],
)
def test_a_statement_that_changes_or_removes_rows_is_flagged(sql):
    assert dev_upgrade.data_changes(sql)


@pytest.mark.parametrize(
    "sql",
    [
        "ALTER TABLE t ADD COLUMN IF NOT EXISTS c INT NOT NULL DEFAULT 0;",
        "ALTER TABLE t DROP CONSTRAINT IF EXISTS t_check;",
        "ALTER TABLE t ALTER COLUMN c DROP NOT NULL;",
        "ALTER TABLE t ALTER COLUMN c DROP DEFAULT;",
        "DROP INDEX IF EXISTS t_idx;",
        "CREATE TABLE t (a INT REFERENCES agencies(agency_id) ON DELETE CASCADE ON UPDATE CASCADE);",
        "-- DROP TABLE t; is only mentioned here\nCREATE INDEX t_idx ON t (a);",
    ],
)
def test_a_schema_only_statement_is_not_flagged(sql):
    assert dev_upgrade.data_changes(sql) == []


def test_only_pending_migrations_that_change_rows_are_reported(tmp_path):
    (tmp_path / "0001_add.up.sql").write_text("ALTER TABLE t ADD COLUMN c INT;")
    (tmp_path / "0002_drop.up.sql").write_text("ALTER TABLE t DROP COLUMN c;")
    (tmp_path / "0003_drop_applied.up.sql").write_text("DROP TABLE t;")
    found = dev_upgrade.destructive_pending(["0001", "0002"], tmp_path)
    assert list(found) == ["0002_drop"]


def test_the_database_url_is_read_as_the_makefile_reads_it(tmp_path):
    (tmp_path / ".env").write_text("# comment\nOTHER=1\nexport DATABASE_URL='postgresql://u@h:5543/d'\n")
    assert dev_upgrade.dev_database_url(tmp_path) == "postgresql://u@h:5543/d"


def test_no_database_url_is_refused(tmp_path):
    (tmp_path / ".env").write_text("OTHER=1\n")
    with pytest.raises(dev_upgrade.Refusal, match="no DATABASE_URL"):
        dev_upgrade.dev_database_url(tmp_path)


def test_a_pending_migration_that_changes_rows_stops_a_real_run_before_any_step(monkeypatch, capsys):
    monkeypatch.setattr(dev_upgrade, "check_checkout", lambda: None)
    monkeypatch.setattr(dev_upgrade, "dev_database_url", lambda: "postgresql://x")
    monkeypatch.setattr(dev_upgrade, "pending_versions", lambda url: ["0099"])
    monkeypatch.setattr(dev_upgrade, "destructive_pending", lambda pending: {"0099_drop": ["DROP TABLE t"]})
    ran = []
    monkeypatch.setattr(dev_upgrade, "_run", lambda step, log: ran.append(step))
    assert dev_upgrade.main([]) == 3
    assert ran == []
    assert "0099_drop" in capsys.readouterr().err


def test_the_owner_naming_the_version_lets_it_run(monkeypatch, tmp_path):
    monkeypatch.setattr(dev_upgrade, "check_checkout", lambda: None)
    monkeypatch.setattr(dev_upgrade, "dev_database_url", lambda: "postgresql://x")
    monkeypatch.setattr(dev_upgrade, "pending_versions", lambda url: ["0099"])
    monkeypatch.setattr(dev_upgrade, "destructive_pending", lambda pending: {"0099_drop": ["DROP TABLE t"]})
    monkeypatch.setattr(dev_upgrade, "upgrade_lock", lambda: _no_lock())
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
    monkeypatch.setattr(dev_upgrade, "upgrade_lock", lambda: _no_lock())
    monkeypatch.setattr(dev_upgrade, "LOG_DIR", tmp_path)
    ran = []
    monkeypatch.setattr(dev_upgrade, "_run", lambda step, log: ran.append(step))
    assert dev_upgrade.main(["--start", "3"]) == 0
    assert ran == ["analyze-all", "check-aggs"]


def test_a_refused_checkout_writes_nothing(monkeypatch, capsys):
    def refuse():
        raise dev_upgrade.Refusal("x is a linked worktree; run from the primary checkout")

    monkeypatch.setattr(dev_upgrade, "check_checkout", refuse)
    ran = []
    monkeypatch.setattr(dev_upgrade, "_run", lambda step, log: ran.append(step))
    assert dev_upgrade.main([]) == 3
    assert ran == []
    assert "linked worktree" in capsys.readouterr().err


class _no_lock:
    def __enter__(self):
        return None

    def __exit__(self, *exc):
        return False
