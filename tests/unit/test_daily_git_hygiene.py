"""Tests for the daily git hygiene job (local, remote, and backup-branch cleanup)."""

from __future__ import annotations

import fcntl
import importlib.util
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "daily_git_hygiene.py"
SPEC = importlib.util.spec_from_file_location("daily_git_hygiene", SCRIPT)
assert SPEC and SPEC.loader
hygiene = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = hygiene
SPEC.loader.exec_module(hygiene)

cleanup = hygiene.cleanup_git_state


def git(repo: Path, *args: str) -> str:
    """Run Git in a temporary fixture repository."""

    return subprocess.run(
        ("git", "-C", str(repo), *args),
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def commit_at(repo: Path, message: str, when: str) -> None:
    """Create an empty commit whose author and committer date are both `when` (ISO-8601)."""

    subprocess.run(
        ("git", "-C", str(repo), "commit", "--allow-empty", "-m", message),
        check=True,
        capture_output=True,
        text=True,
        env={**os.environ, "GIT_AUTHOR_DATE": when, "GIT_COMMITTER_DATE": when},
    )


@pytest.fixture
def repository(tmp_path: Path) -> Path:
    """Create a repository whose local main exactly matches a bare `origin`."""

    remote = tmp_path / "remote.git"
    repo = tmp_path / "repo"
    git(tmp_path, "init", "--bare", str(remote))
    git(tmp_path, "init", "-b", "main", str(repo))
    git(repo, "config", "user.name", "Hygiene Test")
    git(repo, "config", "user.email", "hygiene@example.com")
    (repo / "tracked.txt").write_text("main\n", encoding="utf-8")
    git(repo, "add", "tracked.txt")
    git(repo, "commit", "-m", "initial")
    git(repo, "remote", "add", "origin", str(remote))
    git(repo, "push", "-u", "origin", "main")
    return repo


# ---------------------------------------------------------------------------
# Remote branch decision table
# ---------------------------------------------------------------------------


def test_merged_branch_with_no_dependent_pr_is_deletable():
    """A branch whose exact head merged and has no stacked open PR is safe to delete."""

    decision = hygiene.decide_remote_branch(
        "vps-loop/item-7",
        (cleanup.PullRequest(70, "MERGED", "a" * 40),),
        (),
    )

    assert decision.action == "delete"
    assert "#70" in decision.reason


def test_merged_branch_with_open_dependent_pr_is_retained():
    """A stacked PR still open against this branch as its base blocks deletion."""

    decision = hygiene.decide_remote_branch(
        "vps-loop/item-7",
        (cleanup.PullRequest(70, "MERGED", "a" * 40),),
        (71,),
    )

    assert decision.action == "keep"
    assert "#71" in decision.reason


def test_branch_with_its_own_open_pr_is_retained():
    """A branch whose own PR is still open is never touched."""

    decision = hygiene.decide_remote_branch(
        "vps-loop/item-8",
        (cleanup.PullRequest(80, "OPEN", "a" * 40),),
        (),
    )

    assert decision.action == "keep"
    assert "#80" in decision.reason


def test_branch_with_no_pr_history_is_retained():
    """A stray manually-pushed branch with no PR trail at all is a human's call."""

    decision = hygiene.decide_remote_branch("vps-loop/item-9", (), ())

    assert decision.action == "keep"
    assert "no merged PR evidence" in decision.reason


def test_branch_with_only_closed_pr_is_retained():
    """An unmerged closed PR is not proof of recoverability."""

    decision = hygiene.decide_remote_branch(
        "vps-loop/item-10",
        (cleanup.PullRequest(90, "CLOSED", "a" * 40),),
        (),
    )

    assert decision.action == "keep"


# ---------------------------------------------------------------------------
# Backup-branch retention decision table
# ---------------------------------------------------------------------------


def test_backup_branch_younger_than_retention_is_retained():
    """A superseded-backup branch inside the retention window is kept."""

    now = 1_000_000
    commit_epoch = now - (29 * 86400)  # 29 days old, under a 30-day window

    decision = hygiene.decide_backup_branch(
        "vps-loop/item-5-superseded-abc1234", commit_epoch, now_epoch=now, retention_days=30
    )

    assert decision.action == "keep"


def test_backup_branch_exactly_at_retention_boundary_is_retained():
    """Exactly at the retention window is not yet 'older than' it."""

    now = 1_000_000
    commit_epoch = now - (30 * 86400)

    decision = hygiene.decide_backup_branch(
        "vps-loop/item-5-superseded-abc1234", commit_epoch, now_epoch=now, retention_days=30
    )

    assert decision.action == "keep"


def test_backup_branch_older_than_retention_is_deletable():
    """A superseded-backup branch past the retention window is deletable."""

    now = 1_000_000
    commit_epoch = now - (31 * 86400)

    decision = hygiene.decide_backup_branch(
        "vps-loop/item-5-superseded-abc1234", commit_epoch, now_epoch=now, retention_days=30
    )

    assert decision.action == "delete"
    assert "31.0d old" in decision.reason


# ---------------------------------------------------------------------------
# Branch name matching
# ---------------------------------------------------------------------------


def test_superseded_pattern_matches_only_the_backup_shape():
    """The backup-branch regex must not also match a plain item branch or unrelated name."""

    assert hygiene.SUPERSEDED_BRANCH_RE.match("vps-loop/item-12-superseded-abc123f")
    assert not hygiene.SUPERSEDED_BRANCH_RE.match("vps-loop/item-12")
    assert not hygiene.SUPERSEDED_BRANCH_RE.match("vps-loop/item-12-superseded")
    assert not hygiene.VPS_LOOP_ITEM_BRANCH_RE.match("vps-loop/item-12-superseded-abc123f")
    assert hygiene.VPS_LOOP_ITEM_BRANCH_RE.match("vps-loop/item-12")


# ---------------------------------------------------------------------------
# Lock file concurrency guard
# ---------------------------------------------------------------------------


def test_lock_round_trip_when_uncontended(tmp_path: Path):
    """Acquiring and releasing an uncontended lock works and frees it for reuse."""

    lock_path = tmp_path / "claude-loop.lock"

    handle = hygiene.try_acquire_lock(lock_path)
    assert handle is not None
    hygiene.release_lock(handle)

    # Freed after release: a second acquire must succeed.
    second = hygiene.try_acquire_lock(lock_path)
    assert second is not None
    hygiene.release_lock(second)


def test_lock_skip_when_already_held(tmp_path: Path):
    """A concurrently held lock (simulating a live /vps-loop-run tick) is not acquired."""

    lock_path = tmp_path / "claude-loop.lock"
    holder = lock_path.open("a+")
    fcntl.flock(holder, fcntl.LOCK_EX | fcntl.LOCK_NB)
    try:
        assert hygiene.try_acquire_lock(lock_path) is None
    finally:
        fcntl.flock(holder, fcntl.LOCK_UN)
        holder.close()


def test_main_skips_all_cleanup_when_lock_is_held(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """main() must not touch any git state at all while the loop's lock is held."""

    lock_path = tmp_path / "claude-loop.lock"
    log_path = tmp_path / "git-hygiene.log"
    holder = lock_path.open("a+")
    fcntl.flock(holder, fcntl.LOCK_EX | fcntl.LOCK_NB)

    def _boom(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("cleanup stage must not run while the loop lock is held")

    monkeypatch.setattr(hygiene, "run_local_cleanup", _boom)
    monkeypatch.setattr(hygiene, "run_remote_branch_cleanup", _boom)
    monkeypatch.setattr(hygiene, "run_backup_branch_pruning", _boom)

    try:
        exit_code = hygiene.main(
            [
                "--repo",
                str(tmp_path),
                "--lock-file",
                str(lock_path),
                "--log-file",
                str(log_path),
                "--apply",
            ]
        )
    finally:
        fcntl.flock(holder, fcntl.LOCK_UN)
        holder.close()

    assert exit_code == 0
    assert "SKIP" in log_path.read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# End-to-end: backup-branch pruning against a real repository
# ---------------------------------------------------------------------------


def test_branch_commit_epoch_reads_committer_time(repository: Path):
    """`branch_commit_epoch` returns the exact `%ct` of the branch tip."""

    when = "2000-01-01T00:00:00+0000"
    git(repository, "branch", "vps-loop/item-1-superseded-deadbee", "main")
    git(repository, "checkout", "vps-loop/item-1-superseded-deadbee")
    commit_at(repository, "old superseded backup", when)
    git(repository, "checkout", "main")

    epoch = hygiene.branch_commit_epoch(repository, "vps-loop/item-1-superseded-deadbee")

    assert epoch == 946_684_800


def test_run_backup_branch_pruning_deletes_only_stale_branches(tmp_path: Path, repository: Path):
    """Apply removes only the superseded-backup branch older than the retention window."""

    now = time.time()
    old_when = time.strftime("%Y-%m-%dT%H:%M:%S+0000", time.gmtime(now - 40 * 86400))
    new_when = time.strftime("%Y-%m-%dT%H:%M:%S+0000", time.gmtime(now - 5 * 86400))

    git(repository, "branch", "vps-loop/item-1-superseded-deadbee", "main")
    git(repository, "checkout", "vps-loop/item-1-superseded-deadbee")
    commit_at(repository, "old superseded backup", old_when)
    git(repository, "checkout", "main")

    git(repository, "branch", "vps-loop/item-2-superseded-cafef00d", "main")
    git(repository, "checkout", "vps-loop/item-2-superseded-cafef00d")
    commit_at(repository, "recent superseded backup", new_when)
    git(repository, "checkout", "main")

    log_path = tmp_path / "git-hygiene.log"
    hygiene.run_backup_branch_pruning(repository, retention_days=30, apply=True, log_file=log_path)

    assert git(repository, "branch", "--list", "vps-loop/item-1-superseded-deadbee") == ""
    assert git(repository, "branch", "--list", "vps-loop/item-2-superseded-cafef00d")
    log_contents = log_path.read_text(encoding="utf-8")
    assert "vps-loop/item-1-superseded-deadbee" in log_contents
    assert "vps-loop/item-2-superseded-cafef00d" not in log_contents


def test_run_backup_branch_pruning_dry_run_deletes_nothing(tmp_path: Path, repository: Path):
    """Without --apply, planning must not remove anything nor write the log."""

    old_when = time.strftime("%Y-%m-%dT%H:%M:%S+0000", time.gmtime(time.time() - 40 * 86400))
    git(repository, "branch", "vps-loop/item-3-superseded-1234567", "main")
    git(repository, "checkout", "vps-loop/item-3-superseded-1234567")
    commit_at(repository, "old superseded backup", old_when)
    git(repository, "checkout", "main")

    log_path = tmp_path / "git-hygiene.log"
    hygiene.run_backup_branch_pruning(repository, retention_days=30, apply=False, log_file=log_path)

    assert git(repository, "branch", "--list", "vps-loop/item-3-superseded-1234567")
    assert not log_path.exists()


# ---------------------------------------------------------------------------
# End-to-end: remote branch cleanup against a real (local, bare) "origin"
# ---------------------------------------------------------------------------


def test_run_remote_branch_cleanup_deletes_only_merged_non_stacked_branches(
    tmp_path: Path, repository: Path, monkeypatch: pytest.MonkeyPatch
):
    """Apply deletes a merged, non-stacked branch but keeps one with a live dependent PR."""

    git(repository, "push", "origin", "HEAD:refs/heads/vps-loop/item-11")
    git(repository, "push", "origin", "HEAD:refs/heads/vps-loop/item-12")
    git(repository, "push", "origin", "HEAD:refs/heads/vps-loop/item-13")

    pull_requests = {
        "vps-loop/item-11": (cleanup.PullRequest(111, "MERGED", "a" * 40),),
        "vps-loop/item-12": (cleanup.PullRequest(112, "MERGED", "a" * 40),),
        # item-13 has no PR at all -- must never be touched.
    }
    dependents = {
        "vps-loop/item-11": (),
        "vps-loop/item-12": (120,),
        "vps-loop/item-13": (),
    }
    monkeypatch.setattr(cleanup, "load_pull_requests", lambda _repo: pull_requests)
    monkeypatch.setattr(hygiene, "dependent_open_pr_numbers", lambda _repo, branch: dependents[branch])

    log_path = tmp_path / "git-hygiene.log"
    hygiene.run_remote_branch_cleanup(repository, remote="origin", apply=True, log_file=log_path)

    remaining = git(repository, "ls-remote", "--heads", "origin", "vps-loop/item-*")
    assert "vps-loop/item-11" not in remaining
    assert "vps-loop/item-12" in remaining
    assert "vps-loop/item-13" in remaining
    log_contents = log_path.read_text(encoding="utf-8")
    assert "vps-loop/item-11" in log_contents
    assert "vps-loop/item-12" not in log_contents


def test_run_remote_branch_cleanup_dry_run_deletes_nothing(
    tmp_path: Path, repository: Path, monkeypatch: pytest.MonkeyPatch
):
    """Without --apply, planning must not delete any remote branch nor write the log."""

    git(repository, "push", "origin", "HEAD:refs/heads/vps-loop/item-20")
    pull_requests = {"vps-loop/item-20": (cleanup.PullRequest(200, "MERGED", "a" * 40),)}
    monkeypatch.setattr(cleanup, "load_pull_requests", lambda _repo: pull_requests)
    monkeypatch.setattr(hygiene, "dependent_open_pr_numbers", lambda _repo, _branch: ())

    log_path = tmp_path / "git-hygiene.log"
    hygiene.run_remote_branch_cleanup(repository, remote="origin", apply=False, log_file=log_path)

    remaining = git(repository, "ls-remote", "--heads", "origin", "vps-loop/item-*")
    assert "vps-loop/item-20" in remaining
    assert not log_path.exists()
