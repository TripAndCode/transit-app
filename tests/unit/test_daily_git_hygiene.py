"""Tests for the daily git hygiene job (local, remote, backup-branch, and venv cleanup)."""

from __future__ import annotations

import fcntl
import importlib.util
import os
import shutil
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

    # `update-ref` requires the target object to already exist in `repository`'s own
    # object database -- it won't fetch it for us. Fetch the bare SHA directly (not
    # through the `origin` remote) so the object lands locally without moving
    # `refs/remotes/origin/main`, which is exactly the ref this test needs to stay stale.
    git(repository, "fetch", str(tmp_path / "remote.git"), new_tip)
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
    state_path = tmp_path / "last-success"
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
                "--state-file",
                str(state_path),
                "--apply",
            ]
        )
    finally:
        fcntl.flock(holder, fcntl.LOCK_UN)
        holder.close()

    assert exit_code == 0
    assert "SKIP" in log_path.read_text(encoding="utf-8")
    # A lock-collision skip is not a completion -- must still retry later today.
    assert not state_path.exists()


# ---------------------------------------------------------------------------
# Same-day completion marker: turns a lost lock race into an hourly retry
# instead of a 24-hour one (see `already_succeeded_today`'s own docstring).
# ---------------------------------------------------------------------------


def test_already_succeeded_today_false_when_state_file_missing(tmp_path: Path):
    """No prior run recorded at all -- must not be mistaken for success."""

    assert hygiene.already_succeeded_today(tmp_path / "missing") is False


def test_mark_and_check_succeeded_today_round_trip(tmp_path: Path):
    """Marking success today is what the same-day check then reports back."""

    state_path = tmp_path / "last-success"
    hygiene.mark_succeeded_today(state_path, tmp_path / "git-hygiene.log")
    assert hygiene.already_succeeded_today(state_path) is True


def test_already_succeeded_today_false_for_a_stale_prior_day(tmp_path: Path):
    """A completion recorded on an earlier calendar day does not count for today."""

    state_path = tmp_path / "last-success"
    state_path.write_text("2000-01-01\n", encoding="utf-8")
    assert hygiene.already_succeeded_today(state_path) is False


def test_already_succeeded_today_fails_open_on_a_non_missing_os_error(tmp_path: Path):
    """A permission/directory-shape OSError reading the state file must not crash the job.

    Every stage this gates is already idempotent, so treating "can't tell" the same
    as "not yet succeeded" costs at most one redundant hourly retry, not a false skip.
    """

    state_path = tmp_path / "last-success-is-a-directory"
    state_path.mkdir()  # read_text() on a directory raises IsADirectoryError (an OSError)
    assert hygiene.already_succeeded_today(state_path) is False


def test_mark_succeeded_today_logs_instead_of_raising_on_write_failure(tmp_path: Path):
    """A write failure must be logged, not raised -- this runs after cleanup already succeeded."""

    state_path = tmp_path / "last-success-parent-is-a-file"
    state_path.parent.mkdir(parents=True, exist_ok=True)
    blocking_file = state_path.parent / "blocking"
    blocking_file.write_text("", encoding="utf-8")
    unwritable_state_path = blocking_file / "last-success"  # parent.mkdir() fails: not a directory

    log_path = tmp_path / "git-hygiene.log"
    hygiene.mark_succeeded_today(unwritable_state_path, log_path)  # must not raise

    assert "WARNING" in log_path.read_text(encoding="utf-8")


def test_already_succeeded_today_and_mark_succeeded_today_use_the_given_day_not_the_live_clock(
    tmp_path: Path,
):
    """An explicit `today=` must be used verbatim, so a run straddling a UTC midnight stays
    self-consistent between its pre-check and its eventual marker (both pass the same
    captured day, rather than each calling `today_utc()` fresh)."""

    state_path = tmp_path / "last-success"
    log_path = tmp_path / "git-hygiene.log"

    hygiene.mark_succeeded_today(state_path, log_path, today="2026-01-01")

    assert hygiene.already_succeeded_today(state_path, today="2026-01-01") is True
    # A different (e.g. live "today") day must not match yesterday's explicit marker.
    assert hygiene.already_succeeded_today(state_path, today="2026-01-02") is False


def test_main_skips_before_touching_the_lock_when_already_succeeded_today(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """An hourly re-trigger on a day this job already completed must not even try the lock."""

    lock_path = tmp_path / "claude-loop.lock"
    log_path = tmp_path / "git-hygiene.log"
    state_path = tmp_path / "last-success"
    hygiene.mark_succeeded_today(state_path, log_path)

    def _boom(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("must not attempt the lock once today's run is already marked complete")

    monkeypatch.setattr(hygiene, "try_acquire_lock", _boom)

    exit_code = hygiene.main(
        [
            "--repo",
            str(tmp_path),
            "--lock-file",
            str(lock_path),
            "--log-file",
            str(log_path),
            "--state-file",
            str(state_path),
            "--apply",
        ]
    )

    assert exit_code == 0


def test_main_marks_today_succeeded_only_after_a_fully_clean_apply_run(
    tmp_path: Path, repository: Path, monkeypatch: pytest.MonkeyPatch
):
    """A stage error must not mark today done -- it should retry on the next hourly trigger."""

    lock_path = tmp_path / "claude-loop.lock"
    log_path = tmp_path / "git-hygiene.log"
    state_path = tmp_path / "last-success"

    def _boom(*_args: object, **_kwargs: object) -> bool:
        raise hygiene.HygieneError("simulated stage failure")

    monkeypatch.setattr(hygiene, "run_remote_branch_cleanup", _boom)

    exit_code = hygiene.main(
        [
            "--repo",
            str(repository),
            "--lock-file",
            str(lock_path),
            "--log-file",
            str(log_path),
            "--state-file",
            str(state_path),
            "--apply",
        ]
    )

    assert exit_code == 2
    assert not state_path.exists()


def test_main_does_not_mark_today_succeeded_on_a_dry_run(
    tmp_path: Path, repository: Path, monkeypatch: pytest.MonkeyPatch
):
    """A plain preview (no --apply, the documented default) must not poison the day's real cron run.

    Every stage returns True/exit_code 0 on a dry run just as easily as on a genuinely
    clean --apply run, since each returns before deleting anything -- without the
    `args.apply` gate, an operator previewing the plan by hand would silently cancel
    that day's scheduled --apply cleanup.
    """

    monkeypatch.setattr(cleanup, "load_pull_requests", lambda _repo: {})
    monkeypatch.setattr(hygiene, "load_dependent_open_prs", lambda _repo: {})

    lock_path = tmp_path / "claude-loop.lock"
    log_path = tmp_path / "git-hygiene.log"
    state_path = tmp_path / "last-success"

    exit_code = hygiene.main(
        [
            "--repo",
            str(repository),
            "--lock-file",
            str(lock_path),
            "--log-file",
            str(log_path),
            "--state-file",
            str(state_path),
        ]
    )

    assert exit_code == 0
    assert not state_path.exists()


def test_main_marks_today_succeeded_after_a_fully_clean_apply_run_with_nothing_to_clean(
    tmp_path: Path, repository: Path, monkeypatch: pytest.MonkeyPatch
):
    """A real --apply run that finds nothing to delete is still a fully-clean day."""

    monkeypatch.setattr(cleanup, "load_pull_requests", lambda _repo: {})
    monkeypatch.setattr(hygiene, "load_dependent_open_prs", lambda _repo: {})

    lock_path = tmp_path / "claude-loop.lock"
    log_path = tmp_path / "git-hygiene.log"
    state_path = tmp_path / "last-success"

    exit_code = hygiene.main(
        [
            "--repo",
            str(repository),
            "--lock-file",
            str(lock_path),
            "--log-file",
            str(log_path),
            "--state-file",
            str(state_path),
            "--apply",
        ]
    )

    assert exit_code == 0
    assert hygiene.already_succeeded_today(state_path) is True


def test_main_does_not_mark_today_succeeded_when_a_stage_has_a_per_item_failure(
    tmp_path: Path, repository: Path, monkeypatch: pytest.MonkeyPatch
):
    """A stage that returns False (a swallowed per-item error) must not mark today done either."""

    lock_path = tmp_path / "claude-loop.lock"
    log_path = tmp_path / "git-hygiene.log"
    state_path = tmp_path / "last-success"

    monkeypatch.setattr(hygiene, "run_remote_branch_cleanup", lambda *_args, **_kwargs: False)

    exit_code = hygiene.main(
        [
            "--repo",
            str(repository),
            "--lock-file",
            str(lock_path),
            "--log-file",
            str(log_path),
            "--state-file",
            str(state_path),
            "--apply",
        ]
    )

    assert exit_code == 2
    assert not state_path.exists()


# ---------------------------------------------------------------------------
# End-to-end: backup-branch pruning against a real repository
# ---------------------------------------------------------------------------


def test_load_local_superseded_branches_reads_committer_time_in_bulk(repository: Path):
    """One `for-each-ref` call returns the exact committer-time epoch per backup branch,
    and excludes a plain (non-superseded) `vps-loop/item-<N>` branch.
    """

    when = "2000-01-01T00:00:00+0000"
    git(repository, "branch", "vps-loop/item-1-superseded-deadbee", "main")
    git(repository, "checkout", "vps-loop/item-1-superseded-deadbee")
    commit_at(repository, "old superseded backup", when)
    git(repository, "checkout", "main")
    git(repository, "branch", "vps-loop/item-2", "main")  # not a backup branch; must be excluded

    branches = hygiene.load_local_superseded_branches(repository)

    assert branches == {"vps-loop/item-1-superseded-deadbee": 946_684_800}


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


def test_run_remote_branch_cleanup_skips_branch_whose_tip_changed_since_planning(
    tmp_path: Path, repository: Path, monkeypatch: pytest.MonkeyPatch
):
    """A commit landing on the branch between planning and delete must not be discarded.

    Simulates the TOCTOU window entirely within one `run_remote_branch_cleanup` call:
    `list_remote_vps_loop_branches` is patched to return the tip as it was at planning
    time, while the branch is genuinely advanced on `origin` before the apply loop's own
    real, unpatched `remote_branch_head` recheck runs.
    """

    planned_tip = git(repository, "rev-parse", "HEAD")
    git(repository, "push", "origin", "HEAD:refs/heads/vps-loop/item-17")

    monkeypatch.setattr(
        hygiene, "list_remote_vps_loop_branches", lambda _repo, _remote: {"vps-loop/item-17": planned_tip}
    )
    pull_requests = {"vps-loop/item-17": (cleanup.PullRequest(117, "MERGED", planned_tip),)}
    monkeypatch.setattr(cleanup, "load_pull_requests", lambda _repo: pull_requests)
    monkeypatch.setattr(hygiene, "load_dependent_open_prs", lambda _repo: {})

    # A real new commit lands on the branch on `origin` after planning would have run.
    (repository / "tracked.txt").write_text("advanced on the branch\n", encoding="utf-8")
    git(repository, "commit", "-am", "advance the branch after planning")
    git(repository, "push", "origin", "HEAD:refs/heads/vps-loop/item-17")
    advanced_tip = git(repository, "rev-parse", "HEAD")
    assert advanced_tip != planned_tip

    log_path = tmp_path / "git-hygiene.log"
    hygiene.run_remote_branch_cleanup(repository, remote="origin", apply=True, log_file=log_path)

    remaining = git(repository, "ls-remote", "--heads", "origin", "vps-loop/item-17")
    assert advanced_tip in remaining  # branch survives with its newer, unshipped commit intact
    log_contents = log_path.read_text(encoding="utf-8")
    assert "tip changed since planning" in log_contents


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
    all_clean = hygiene.run_remote_branch_cleanup(repository, remote="origin", apply=True, log_file=log_path)

    assert all_clean is False  # the swallowed per-branch error must still surface to the caller
    remaining = git(repository, "ls-remote", "--heads", "origin", "vps-loop/item-*")
    assert "vps-loop/item-15" in remaining  # the failed delete leaves it in place
    assert "vps-loop/item-16" not in remaining  # the other branch still gets swept
    log_contents = log_path.read_text(encoding="utf-8")
    assert "ERROR deleting origin/vps-loop/item-15" in log_contents
    assert "DELETED origin/vps-loop/item-16" in log_contents


def test_run_remote_branch_cleanup_continues_after_one_tip_check_error(
    tmp_path: Path, repository: Path, monkeypatch: pytest.MonkeyPatch
):
    """A transient failure checking one branch's tip must not stop the rest of the stage.

    Distinct from the delete-failure test above: this injects the failure at the
    immediately-before-delete `remote_branch_head` recheck itself, not at
    `delete_remote_branch`.
    """

    tip = git(repository, "rev-parse", "HEAD")
    git(repository, "push", "origin", "HEAD:refs/heads/vps-loop/item-18")
    git(repository, "push", "origin", "HEAD:refs/heads/vps-loop/item-19")

    pull_requests = {
        "vps-loop/item-18": (cleanup.PullRequest(118, "MERGED", tip),),
        "vps-loop/item-19": (cleanup.PullRequest(119, "MERGED", tip),),
    }
    monkeypatch.setattr(cleanup, "load_pull_requests", lambda _repo: pull_requests)
    monkeypatch.setattr(hygiene, "load_dependent_open_prs", lambda _repo: {})

    real_remote_branch_head = hygiene.remote_branch_head

    def _flaky_head_check(repo: Path, remote: str, branch: str) -> str | None:
        if branch == "vps-loop/item-18":
            raise cleanup.CleanupError("simulated transient network error")
        return real_remote_branch_head(repo, remote, branch)

    monkeypatch.setattr(hygiene, "remote_branch_head", _flaky_head_check)

    log_path = tmp_path / "git-hygiene.log"
    all_clean = hygiene.run_remote_branch_cleanup(repository, remote="origin", apply=True, log_file=log_path)

    assert all_clean is False  # the swallowed per-branch error must still surface to the caller
    remaining = git(repository, "ls-remote", "--heads", "origin", "vps-loop/item-*")
    assert "vps-loop/item-18" in remaining  # the failed tip check leaves it in place
    assert "vps-loop/item-19" not in remaining  # the other branch still gets checked and swept
    log_contents = log_path.read_text(encoding="utf-8")
    assert "ERROR checking origin/vps-loop/item-18 tip" in log_contents
    assert "DELETED origin/vps-loop/item-19" in log_contents


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
    all_clean = hygiene.run_backup_branch_pruning(repository, retention_days=30, apply=True, log_file=log_path)

    assert all_clean is False  # the swallowed per-branch error must still surface to the caller
    assert git(repository, "branch", "--list", "vps-loop/item-6-superseded-1111111")
    assert git(repository, "branch", "--list", "vps-loop/item-6-superseded-2222222") == ""
    log_contents = log_path.read_text(encoding="utf-8")
    assert "ERROR deleting vps-loop/item-6-superseded-1111111" in log_contents
    assert "DELETED vps-loop/item-6-superseded-2222222" in log_contents


# ---------------------------------------------------------------------------
# 4. Orphaned poetry venv pruning
# ---------------------------------------------------------------------------


def _make_fake_venv(root: Path, name: str, *, age_hours: float) -> Path:
    """Create a directory standing in for a poetry venv, with a specific mtime."""

    venv = root / name
    venv.mkdir(parents=True)
    (venv / "pyvenv.cfg").write_text("home = /usr/bin\n", encoding="utf-8")
    stamp = time.time() - age_hours * 3600
    os.utime(venv, (stamp, stamp))
    return venv


def test_poetry_env_path_returns_none_on_failure(monkeypatch: pytest.MonkeyPatch):
    """A location with no resolvable poetry venv (not a poetry project, no venv yet) is not an error."""

    monkeypatch.setattr(
        cleanup, "run_command", lambda *_a, **_k: subprocess.CompletedProcess((), returncode=1, stdout="", stderr="")
    )
    assert hygiene.poetry_env_path(Path("/unused")) is None


def test_poetry_env_path_returns_resolved_path_on_success(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """A successful `poetry env info --path` is parsed and resolved."""

    venv = tmp_path / "some-venv"
    venv.mkdir()
    monkeypatch.setattr(
        cleanup,
        "run_command",
        lambda *_a, **_k: subprocess.CompletedProcess((), returncode=0, stdout=f"{venv}\n", stderr=""),
    )
    assert hygiene.poetry_env_path(Path("/unused")) == venv.resolve()


def test_compute_in_use_poetry_venvs_raises_when_main_repo_venv_unresolvable(
    repository: Path, monkeypatch: pytest.MonkeyPatch
):
    """Refuses to compute an in-use set at all if the main checkout's own venv can't be found --
    an empty/partial in-use set would make every real venv look orphaned."""

    monkeypatch.setattr(hygiene, "poetry_env_path", lambda _location: None)
    with pytest.raises(hygiene.HygieneError, match="main checkout"):
        hygiene.compute_in_use_poetry_venvs(repository)


def test_compute_in_use_poetry_venvs_includes_main_and_every_worktree(
    tmp_path: Path, repository: Path, monkeypatch: pytest.MonkeyPatch
):
    """The in-use set covers the main repo plus each worktree, by whatever venv path
    `poetry_env_path` reports for that location -- worktrees with no resolvable venv
    yet are simply absent from the set, not an error."""

    worktree_path = tmp_path / "extra-worktree"
    git(repository, "worktree", "add", "-b", "vps-loop/item-99", str(worktree_path), "main")

    main_venv = tmp_path / "venv-main"
    worktree_venv = tmp_path / "venv-item-99"

    def _fake_env_path(location: Path) -> Path | None:
        if location.resolve() == repository.resolve():
            return main_venv
        if location.resolve() == worktree_path.resolve():
            return worktree_venv
        return None

    monkeypatch.setattr(hygiene, "poetry_env_path", _fake_env_path)

    in_use = hygiene.compute_in_use_poetry_venvs(repository)

    assert in_use == {main_venv, worktree_venv}


def test_run_orphaned_venv_pruning_is_a_noop_when_venv_root_missing(tmp_path: Path):
    """No venv root at all (e.g. poetry never ran here) is not an error."""

    assert hygiene.run_orphaned_venv_pruning(
        tmp_path, venv_root=tmp_path / "does-not-exist", min_age_hours=24, apply=True, log_file=tmp_path / "log"
    ) is True


def test_run_orphaned_venv_pruning_dry_run_deletes_nothing(
    tmp_path: Path, repository: Path, monkeypatch: pytest.MonkeyPatch
):
    """Without --apply, planning must not delete any venv nor write the log."""

    venv_root = tmp_path / "virtualenvs"
    orphaned = _make_fake_venv(venv_root, "transit-delay-app-orphan-py3.12", age_hours=100)
    monkeypatch.setattr(hygiene, "compute_in_use_poetry_venvs", lambda _repo: set())

    log_path = tmp_path / "git-hygiene.log"
    all_clean = hygiene.run_orphaned_venv_pruning(
        repository, venv_root=venv_root, min_age_hours=24, apply=False, log_file=log_path
    )

    assert all_clean is True
    assert orphaned.is_dir()
    assert not log_path.exists()


def test_run_orphaned_venv_pruning_deletes_old_orphans_keeps_in_use_and_young(
    tmp_path: Path, repository: Path, monkeypatch: pytest.MonkeyPatch
):
    """Only a venv that is BOTH unmatched by any current worktree AND older than the
    safety margin gets deleted; an in-use one and a too-young orphan are both kept."""

    venv_root = tmp_path / "virtualenvs"
    in_use_venv = _make_fake_venv(venv_root, "transit-delay-app-inuse-py3.12", age_hours=1000)
    young_orphan = _make_fake_venv(venv_root, "transit-delay-app-young-py3.12", age_hours=1)
    old_orphan = _make_fake_venv(venv_root, "transit-delay-app-old-py3.12", age_hours=100)

    monkeypatch.setattr(hygiene, "compute_in_use_poetry_venvs", lambda _repo: {in_use_venv.resolve()})

    log_path = tmp_path / "git-hygiene.log"
    all_clean = hygiene.run_orphaned_venv_pruning(
        repository, venv_root=venv_root, min_age_hours=24, apply=True, log_file=log_path
    )

    assert all_clean is True
    assert in_use_venv.is_dir()
    assert young_orphan.is_dir()
    assert not old_orphan.exists()
    assert "DELETED" in log_path.read_text(encoding="utf-8")
    assert "transit-delay-app-old-py3.12" in log_path.read_text(encoding="utf-8")


def test_run_orphaned_venv_pruning_rechecks_before_apply(
    tmp_path: Path, repository: Path, monkeypatch: pytest.MonkeyPatch
):
    """A venv that became in-use between planning and apply must not be deleted --
    mirrors run_remote_branch_cleanup's fresh_dependents re-check."""

    venv_root = tmp_path / "virtualenvs"
    candidate = _make_fake_venv(venv_root, "transit-delay-app-newly-adopted-py3.12", age_hours=100)

    calls = {"n": 0}

    def _fake_in_use(_repo: Path) -> set[Path]:
        calls["n"] += 1
        # Planning (1st call) sees it as orphaned; the apply-time re-check (2nd call)
        # sees it as now in use, as if a new worktree had just adopted this exact venv.
        return set() if calls["n"] == 1 else {candidate.resolve()}

    monkeypatch.setattr(hygiene, "compute_in_use_poetry_venvs", _fake_in_use)

    log_path = tmp_path / "git-hygiene.log"
    all_clean = hygiene.run_orphaned_venv_pruning(
        repository, venv_root=venv_root, min_age_hours=24, apply=True, log_file=log_path
    )

    assert all_clean is True
    assert candidate.is_dir()
    assert "SKIPPED" in log_path.read_text(encoding="utf-8")
    assert calls["n"] == 2


def test_run_orphaned_venv_pruning_continues_after_one_delete_error(
    tmp_path: Path, repository: Path, monkeypatch: pytest.MonkeyPatch
):
    """A delete failure on one orphaned venv must not stop the rest of the stage."""

    venv_root = tmp_path / "virtualenvs"
    flaky = _make_fake_venv(venv_root, "transit-delay-app-flaky-py3.12", age_hours=100)
    fine = _make_fake_venv(venv_root, "transit-delay-app-fine-py3.12", age_hours=100)

    monkeypatch.setattr(hygiene, "compute_in_use_poetry_venvs", lambda _repo: set())

    real_rmtree = shutil.rmtree

    def _flaky_rmtree(path: object, *args: object, **kwargs: object) -> None:
        if Path(str(path)).name == "transit-delay-app-flaky-py3.12":
            raise OSError("simulated transient error")
        real_rmtree(path, *args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(hygiene.shutil, "rmtree", _flaky_rmtree)

    log_path = tmp_path / "git-hygiene.log"
    all_clean = hygiene.run_orphaned_venv_pruning(
        repository, venv_root=venv_root, min_age_hours=24, apply=True, log_file=log_path
    )

    assert all_clean is False  # the swallowed per-venv error must still surface to the caller
    assert flaky.is_dir()
    assert not fine.exists()
    log_contents = log_path.read_text(encoding="utf-8")
    assert "ERROR deleting" in log_contents
    assert "DELETED" in log_contents
