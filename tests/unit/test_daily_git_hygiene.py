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
# End-to-end: local cleanup delegates to cleanup_git_state
# ---------------------------------------------------------------------------


def test_run_local_cleanup_fetches_remote_before_validating_base(
    tmp_path: Path, repository: Path, monkeypatch: pytest.MonkeyPatch
):
    """`validate_base` requires local `main` to exactly match `refs/remotes/origin/main`;
    this job must refresh that tracking ref itself rather than trust a stale one, since it
    is specifically meant to run during idle stretches nothing else has refreshed recently.
    """

    # Advance the bare `origin`'s main from a second clone, then fast-forward the
    # fixture repo's local `main` to the same commit WITHOUT fetching -- this leaves
    # `refs/remotes/origin/main` stale relative to both `origin` and local `main`.
    other = tmp_path / "other-clone"
    # `--branch main` is explicit because the bare `remote.git`'s own HEAD symref was
    # never repointed off whatever `init.defaultBranch` picked at `init --bare` time --
    # only `refs/heads/main` itself was ever pushed there.
    git(tmp_path, "clone", "--branch", "main", str(tmp_path / "remote.git"), str(other))
    git(other, "config", "user.name", "Other Clone")
    git(other, "config", "user.email", "other@example.com")
    (other / "tracked.txt").write_text("advanced\n", encoding="utf-8")
    git(other, "commit", "-am", "advance origin main")
    git(other, "push", "origin", "main")
    new_tip = git(other, "rev-parse", "HEAD")

    git(repository, "update-ref", "refs/heads/main", new_tip)
    assert git(repository, "rev-parse", "refs/remotes/origin/main") != new_tip

    monkeypatch.setattr(cleanup, "load_pull_requests", lambda _repo: {})
    log_path = tmp_path / "git-hygiene.log"

    # Must not raise `CleanupError("... does not match origin/main")`: run_local_cleanup's
    # own `git fetch --prune origin` should refresh the tracking ref before build_plan runs.
    hygiene.run_local_cleanup(
        repository, base="main", remote="origin", protected={"main", "production"}, apply=False, log_file=log_path
    )

    assert git(repository, "rev-parse", "refs/remotes/origin/main") == new_tip


def test_run_local_cleanup_logs_deleted_local_branches_after_apply(
    tmp_path: Path, repository: Path, monkeypatch: pytest.MonkeyPatch
):
    """Apply deletes a stale local branch and records `cleanup_git_state`'s own DELETED line."""

    git(repository, "branch", "stale-branch", "main")
    monkeypatch.setattr(cleanup, "load_pull_requests", lambda _repo: {})

    log_path = tmp_path / "git-hygiene.log"
    hygiene.run_local_cleanup(
        repository, base="main", remote="origin", protected={"main", "production"}, apply=True, log_file=log_path
    )

    assert git(repository, "branch", "--list", "stale-branch") == ""
    log_contents = log_path.read_text(encoding="utf-8")
    assert "local cleanup: DELETED stale-branch" in log_contents


# ---------------------------------------------------------------------------
# Remote branch decision table
# ---------------------------------------------------------------------------


def test_merged_branch_with_matching_tip_and_no_dependent_pr_is_deletable():
    """A branch whose remote tip exactly matches its merged PR's head is safe to delete."""

    decision = hygiene.decide_remote_branch(
        "vps-loop/item-7",
        "a" * 40,
        (cleanup.PullRequest(70, "MERGED", "a" * 40),),
        (),
    )

    assert decision.action == "delete"
    assert "#70" in decision.reason


def test_merged_branch_with_mismatched_tip_is_retained():
    """A merged PR exists, but the branch's current tip is not that PR's exact head --
    real commits may have landed after the merge, so this is not provably recoverable
    the same way `cleanup_git_state.decide_branch` refuses to delete a local branch
    whose tip differs from its merged PR's head_oid.
    """

    decision = hygiene.decide_remote_branch(
        "vps-loop/item-7",
        "b" * 40,
        (cleanup.PullRequest(70, "MERGED", "a" * 40),),
        (),
    )

    assert decision.action == "keep"
    assert "tip differs" in decision.reason


def test_merged_branch_with_open_dependent_pr_is_retained():
    """A stacked PR still open against this branch as its base blocks deletion."""

    decision = hygiene.decide_remote_branch(
        "vps-loop/item-7",
        "a" * 40,
        (cleanup.PullRequest(70, "MERGED", "a" * 40),),
        (71,),
    )

    assert decision.action == "keep"
    assert "#71" in decision.reason


def test_branch_with_its_own_open_pr_is_retained():
    """A branch whose own PR is still open is never touched."""

    decision = hygiene.decide_remote_branch(
        "vps-loop/item-8",
        "a" * 40,
        (cleanup.PullRequest(80, "OPEN", "a" * 40),),
        (),
    )

    assert decision.action == "keep"
    assert "#80" in decision.reason


def test_branch_with_no_pr_history_is_retained():
    """A stray manually-pushed branch with no PR trail at all is a human's call."""

    decision = hygiene.decide_remote_branch("vps-loop/item-9", "a" * 40, (), ())

    assert decision.action == "keep"
    assert "no merged PR evidence" in decision.reason


def test_branch_with_only_closed_pr_is_retained():
    """An unmerged closed PR is not proof of recoverability."""

    decision = hygiene.decide_remote_branch(
        "vps-loop/item-10",
        "a" * 40,
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

    tip = git(repository, "rev-parse", "HEAD")
    git(repository, "push", "origin", "HEAD:refs/heads/vps-loop/item-11")
    git(repository, "push", "origin", "HEAD:refs/heads/vps-loop/item-12")
    git(repository, "push", "origin", "HEAD:refs/heads/vps-loop/item-13")

    pull_requests = {
        "vps-loop/item-11": (cleanup.PullRequest(111, "MERGED", tip),),
        "vps-loop/item-12": (cleanup.PullRequest(112, "MERGED", tip),),
        # item-13 has no PR at all -- must never be touched.
    }
    dependents = {
        "vps-loop/item-12": (120,),
    }
    monkeypatch.setattr(cleanup, "load_pull_requests", lambda _repo: pull_requests)
    monkeypatch.setattr(hygiene, "load_dependent_open_prs", lambda _repo: dependents)

    log_path = tmp_path / "git-hygiene.log"
    hygiene.run_remote_branch_cleanup(repository, remote="origin", apply=True, log_file=log_path)

    remaining = git(repository, "ls-remote", "--heads", "origin", "vps-loop/item-*")
    assert "vps-loop/item-11" not in remaining
    assert "vps-loop/item-12" in remaining
    assert "vps-loop/item-13" in remaining
    log_contents = log_path.read_text(encoding="utf-8")
    assert "vps-loop/item-11" in log_contents
    assert "vps-loop/item-12" not in log_contents


def test_run_remote_branch_cleanup_retains_branch_with_mismatched_tip(
    tmp_path: Path, repository: Path, monkeypatch: pytest.MonkeyPatch
):
    """A merged PR whose head_oid no longer matches the branch's real remote tip is retained."""

    git(repository, "push", "origin", "HEAD:refs/heads/vps-loop/item-14")

    pull_requests = {"vps-loop/item-14": (cleanup.PullRequest(114, "MERGED", "f" * 40),)}
    monkeypatch.setattr(cleanup, "load_pull_requests", lambda _repo: pull_requests)
    monkeypatch.setattr(hygiene, "load_dependent_open_prs", lambda _repo: {})

    log_path = tmp_path / "git-hygiene.log"
    hygiene.run_remote_branch_cleanup(repository, remote="origin", apply=True, log_file=log_path)

    remaining = git(repository, "ls-remote", "--heads", "origin", "vps-loop/item-*")
    assert "vps-loop/item-14" in remaining
    assert not log_path.exists()


def test_run_remote_branch_cleanup_dry_run_deletes_nothing(
    tmp_path: Path, repository: Path, monkeypatch: pytest.MonkeyPatch
):
    """Without --apply, planning must not delete any remote branch nor write the log."""

    tip = git(repository, "rev-parse", "HEAD")
    git(repository, "push", "origin", "HEAD:refs/heads/vps-loop/item-20")
    pull_requests = {"vps-loop/item-20": (cleanup.PullRequest(200, "MERGED", tip),)}
    monkeypatch.setattr(cleanup, "load_pull_requests", lambda _repo: pull_requests)
    monkeypatch.setattr(hygiene, "load_dependent_open_prs", lambda _repo: {})

    log_path = tmp_path / "git-hygiene.log"
    hygiene.run_remote_branch_cleanup(repository, remote="origin", apply=False, log_file=log_path)

    remaining = git(repository, "ls-remote", "--heads", "origin", "vps-loop/item-*")
    assert "vps-loop/item-20" in remaining
    assert not log_path.exists()


def test_load_dependent_open_prs_groups_by_exact_base_ref(monkeypatch: pytest.MonkeyPatch):
    """Grouping trusts only `baseRefName` equality, not gh's own `--base` filter semantics."""

    payload = (
        '[{"number": 5, "baseRefName": "vps-loop/item-1"}, '
        '{"number": 6, "baseRefName": "vps-loop/item-1"}, '
        '{"number": 7, "baseRefName": "vps-loop/item-2"}]'
    )
    fake_result = subprocess.CompletedProcess(args=(), returncode=0, stdout=payload, stderr="")
    monkeypatch.setattr(cleanup, "run_command", lambda *_args, **_kwargs: fake_result)

    grouped = hygiene.load_dependent_open_prs(Path("/unused"))

    assert grouped == {"vps-loop/item-1": (5, 6), "vps-loop/item-2": (7,)}


# ---------------------------------------------------------------------------
# Per-branch delete resilience (one failure must not abort the whole stage)
# ---------------------------------------------------------------------------


def test_run_remote_branch_cleanup_continues_after_one_delete_error(
    tmp_path: Path, repository: Path, monkeypatch: pytest.MonkeyPatch
):
    """A transient delete failure on one branch must not stop the rest of the stage."""

    tip = git(repository, "rev-parse", "HEAD")
    git(repository, "push", "origin", "HEAD:refs/heads/vps-loop/item-15")
    git(repository, "push", "origin", "HEAD:refs/heads/vps-loop/item-16")

    pull_requests = {
        "vps-loop/item-15": (cleanup.PullRequest(115, "MERGED", tip),),
        "vps-loop/item-16": (cleanup.PullRequest(116, "MERGED", tip),),
    }
    monkeypatch.setattr(cleanup, "load_pull_requests", lambda _repo: pull_requests)
    monkeypatch.setattr(hygiene, "load_dependent_open_prs", lambda _repo: {})

    def _flaky_delete(repo: Path, remote: str, branch: str) -> None:
        if branch == "vps-loop/item-15":
            raise cleanup.CleanupError("simulated transient network error")
        cleanup.run_git(repo, "push", remote, "--delete", "--", branch)

    monkeypatch.setattr(hygiene, "delete_remote_branch", _flaky_delete)

    log_path = tmp_path / "git-hygiene.log"
    hygiene.run_remote_branch_cleanup(repository, remote="origin", apply=True, log_file=log_path)

    remaining = git(repository, "ls-remote", "--heads", "origin", "vps-loop/item-*")
    assert "vps-loop/item-15" in remaining  # the failed delete leaves it in place
    assert "vps-loop/item-16" not in remaining  # the other branch still gets swept
    log_contents = log_path.read_text(encoding="utf-8")
    assert "ERROR deleting origin/vps-loop/item-15" in log_contents
    assert "DELETED origin/vps-loop/item-16" in log_contents


def test_run_backup_branch_pruning_continues_after_one_delete_error(
    tmp_path: Path, repository: Path, monkeypatch: pytest.MonkeyPatch
):
    """A delete failure on one stale backup branch must not stop the rest of the stage."""

    old_when = time.strftime("%Y-%m-%dT%H:%M:%S+0000", time.gmtime(time.time() - 40 * 86400))
    for branch in ("vps-loop/item-6-superseded-1111111", "vps-loop/item-6-superseded-2222222"):
        git(repository, "branch", branch, "main")
        git(repository, "checkout", branch)
        commit_at(repository, "old superseded backup", old_when)
        git(repository, "checkout", "main")

    def _flaky_delete(repo: Path, branch: str) -> None:
        if branch == "vps-loop/item-6-superseded-1111111":
            raise cleanup.CleanupError("simulated transient error")
        cleanup.run_git(repo, "branch", "-D", "--", branch)

    monkeypatch.setattr(hygiene, "delete_local_branch", _flaky_delete)

    log_path = tmp_path / "git-hygiene.log"
    hygiene.run_backup_branch_pruning(repository, retention_days=30, apply=True, log_file=log_path)

    assert git(repository, "branch", "--list", "vps-loop/item-6-superseded-1111111")
    assert git(repository, "branch", "--list", "vps-loop/item-6-superseded-2222222") == ""
    log_contents = log_path.read_text(encoding="utf-8")
    assert "ERROR deleting vps-loop/item-6-superseded-1111111" in log_contents
    assert "DELETED vps-loop/item-6-superseded-2222222" in log_contents
