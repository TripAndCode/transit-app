"""Tests for conservative local branch/worktree cleanup."""

from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "cleanup_git_state.py"
SPEC = importlib.util.spec_from_file_location("cleanup_git_state", SCRIPT)
assert SPEC and SPEC.loader
cleanup = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = cleanup
SPEC.loader.exec_module(cleanup)


def git(repo: Path, *args: str) -> str:
    """Run Git in a temporary fixture repository."""

    return subprocess.run(
        ("git", "-C", str(repo), *args),
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


@pytest.fixture
def repository(tmp_path: Path) -> Path:
    """Create a repository whose local main exactly matches origin/main."""

    remote = tmp_path / "remote.git"
    repo = tmp_path / "repo"
    git(tmp_path, "init", "--bare", str(remote))
    git(tmp_path, "init", "-b", "main", str(repo))
    git(repo, "config", "user.name", "Cleanup Test")
    git(repo, "config", "user.email", "cleanup@example.com")
    (repo / "tracked.txt").write_text("main\n", encoding="utf-8")
    git(repo, "add", "tracked.txt")
    git(repo, "commit", "-m", "initial")
    git(repo, "remote", "add", "origin", str(remote))
    git(repo, "push", "-u", "origin", "main")
    # `init --bare` points HEAD at the environment's `init.defaultBranch`
    # (unset in some CI images, defaulting to "master"), independent of
    # which branch actually gets pushed here. Left unpinned, a later
    # `git clone` of this bare repo checks out that stale HEAD target
    # instead of "main" — if no such ref exists, the clone lands on an
    # unborn branch under that name, so a subsequent `git push origin
    # main` in the clone fails with "src refspec main does not match
    # any" (the clone's current branch was never actually named "main").
    git(remote, "symbolic-ref", "HEAD", "refs/heads/main")
    return repo


def facts(**overrides: object):
    """Build minimal facts for decision-table tests."""

    values: dict[str, object] = {
        "branch": "feature",
        "head": "a" * 40,
        "protected": False,
        "current": False,
        "ancestor_of_base": False,
        "tree_matches_base": False,
        "pull_requests": (),
        "worktree": None,
        "dirty": False,
        "head_prs": (),
        "ancestor_of_merged": (),
    }
    values.update(overrides)
    return cleanup.BranchFacts(**values)


@pytest.mark.parametrize(
    ("overrides", "reason"),
    [
        ({"protected": True}, "protected"),
        ({"current": True}, "invoking worktree"),
        ({"pull_requests": (cleanup.PullRequest(10, "OPEN", "a" * 40),)}, "open PR #10"),
        (
            {"worktree": cleanup.Worktree(Path("/tmp/locked"), "feature", locked=True)},
            "worktree is locked",
        ),
        (
            {"worktree": cleanup.Worktree(Path("/tmp/dirty"), "feature"), "dirty": True},
            "uncommitted or untracked",
        ),
    ],
)
def test_protected_or_active_state_is_retained(overrides: dict[str, object], reason: str):
    """No recoverability evidence may override active local state."""

    decision = cleanup.decide_branch(facts(ancestor_of_base=True, **overrides))

    assert decision.action == "keep"
    assert reason in decision.reason


@pytest.mark.parametrize(
    ("overrides", "reason"),
    [
        ({"ancestor_of_base": True}, "ancestor"),
        ({"tree_matches_base": True}, "tree is identical"),
        (
            {"pull_requests": (cleanup.PullRequest(20, "MERGED", "a" * 40),)},
            "merged PR #20",
        ),
    ],
)
def test_only_recoverable_branch_tips_are_deletable(overrides: dict[str, object], reason: str):
    """Ancestry, equal content, or an exact merged PR head proves recoverability."""

    decision = cleanup.decide_branch(facts(**overrides))

    assert decision.action == "delete"
    assert reason in decision.reason


@pytest.mark.parametrize(
    ("pull_requests", "reason"),
    [
        ((cleanup.PullRequest(30, "MERGED", "b" * 40),), "local tip differs"),
        ((cleanup.PullRequest(31, "CLOSED", "a" * 40),), "unmerged closed PR #31"),
        ((), "unique local work"),
    ],
)
def test_unrecoverable_commits_are_retained(pull_requests: tuple[object, ...], reason: str):
    """A merged branch name alone cannot justify deleting a changed local tip."""

    decision = cleanup.decide_branch(facts(pull_requests=pull_requests))

    assert decision.action == "keep"
    assert reason in decision.reason


def test_a_tip_matching_a_merged_pr_opened_from_another_branch_is_deletable():
    """A renamed local branch is still recoverable from the merged PR's permanent head ref."""

    decision = cleanup.decide_branch(
        facts(branch="w571-rebase", head_prs=(cleanup.PullRequest(571, "MERGED", "a" * 40),))
    )

    assert decision.action == "delete"
    assert "merged PR #571" in decision.reason


def test_a_tip_that_heads_an_open_pr_under_another_name_is_retained():
    """Commits outside the base that an open PR is built on stay, whatever the branch is called."""

    decision = cleanup.decide_branch(facts(head_prs=(cleanup.PullRequest(640, "OPEN", "a" * 40),)))

    assert decision.action == "keep"
    assert "open PR #640" in decision.reason


def test_a_base_tip_is_deletable_even_when_an_open_pr_is_headed_there():
    """A release PR headed at main does not pin every local branch that points at main."""

    decision = cleanup.decide_branch(
        facts(ancestor_of_base=True, head_prs=(cleanup.PullRequest(593, "OPEN", "a" * 40),))
    )

    assert decision.action == "delete"
    assert "ancestor of the base" in decision.reason


def test_a_base_tip_worktree_is_retained_while_an_open_pr_is_headed_there():
    """A reviewer of that PR may be standing in the worktree, whatever its branch is called."""

    decision = cleanup.decide_branch(
        facts(
            ancestor_of_base=True,
            worktree=cleanup.Worktree(Path("/tmp/release-prep"), "release-prep"),
            head_prs=(cleanup.PullRequest(593, "OPEN", "a" * 40),),
        )
    )

    assert decision.action == "keep"
    assert "open PR #593" in decision.reason


def test_a_pre_merge_snapshot_of_a_merged_pr_is_deletable():
    """Every commit of a tip that the merged PR head descends from is in that PR."""

    decision = cleanup.decide_branch(
        facts(pull_requests=(cleanup.PullRequest(616, "MERGED", "b" * 40),), ancestor_of_merged=(616,))
    )

    assert decision.action == "delete"
    assert "ancestor of merged PR #616" in decision.reason


def detached(**overrides: object):
    """Build minimal facts for a detached-HEAD worktree."""

    values: dict[str, object] = {
        "worktree": cleanup.Worktree(Path("/tmp/detached"), None, head="c" * 40),
        "primary": False,
        "current": False,
        "dirty": False,
        "review": False,
        "ancestor_of_base": False,
        "tree_matches_base": False,
        "head_prs": (),
        "ancestor_of_merged": (),
    }
    values.update(overrides)
    return cleanup.DetachedFacts(**values)


@pytest.mark.parametrize(
    ("overrides", "action", "reason"),
    [
        ({"ancestor_of_base": True}, "delete", "ancestor of the base"),
        ({"tree_matches_base": True}, "delete", "tree is identical"),
        ({"head_prs": (cleanup.PullRequest(627, "MERGED", "c" * 40),)}, "delete", "merged PR #627"),
        ({"ancestor_of_merged": (633, 634)}, "delete", "ancestor of merged PR #633, #634"),
        ({}, "keep", "no recoverability evidence"),
        ({"ancestor_of_base": True, "primary": True}, "keep", "primary worktree"),
        ({"ancestor_of_base": True, "current": True}, "keep", "invoking worktree"),
        ({"ancestor_of_base": True, "dirty": True}, "keep", "uncommitted or untracked"),
        (
            {"review": True, "head_prs": (cleanup.PullRequest(633, "MERGED", "c" * 40),)},
            "keep",
            "review worktree",
        ),
        (
            {"ancestor_of_base": True, "worktree": cleanup.Worktree(Path("/tmp/d"), None, locked=True, head="c" * 40)},
            "keep",
            "locked",
        ),
        (
            {"ancestor_of_base": True, "head_prs": (cleanup.PullRequest(641, "OPEN", "c" * 40),)},
            "keep",
            "open PR #641",
        ),
    ],
)
def test_detached_worktrees_follow_the_same_recoverability_rules(
    overrides: dict[str, object], action: str, reason: str
):
    """A worktree without a branch is removable only on the evidence a branch would need."""

    decision = cleanup.decide_detached(detached(**overrides))

    assert decision.action == action
    assert decision.branch is None
    assert reason in decision.reason


def test_parse_worktrees_preserves_paths_and_safety_flags(tmp_path: Path):
    """Porcelain parsing keeps paths with spaces, HEADs, and lock/prune metadata."""

    path = tmp_path / "tree with spaces"
    output = (
        f"worktree {path}\nHEAD {'a' * 40}\nbranch refs/heads/feature\nlocked agent running\n\n"
        f"worktree {tmp_path / 'gone'}\nHEAD {'b' * 40}\ndetached\n"
        "prunable gitdir file points to non-existent location\n"
    )

    worktrees = cleanup.parse_worktrees(output)

    assert worktrees[0] == cleanup.Worktree(path.resolve(), "feature", locked=True, head="a" * 40)
    assert worktrees[1].branch is None
    assert worktrees[1].head == "b" * 40
    assert worktrees[1].prunable is True


def test_build_and_apply_plan_remove_only_clean_recoverable_state(
    repository: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """Apply removes a safe branch/worktree while preserving dirty and unique work."""

    clean_path = tmp_path / "clean-tree"
    dirty_path = tmp_path / "dirty-tree"
    git(repository, "branch", "stale-branch", "main")
    git(repository, "worktree", "add", "-b", "stale-worktree", str(clean_path), "main")
    git(repository, "worktree", "add", "-b", "dirty-worktree", str(dirty_path), "main")
    (dirty_path / "notes.txt").write_text("do not discard\n", encoding="utf-8")
    git(repository, "switch", "-c", "unique-work")
    (repository / "tracked.txt").write_text("unique\n", encoding="utf-8")
    git(repository, "commit", "-am", "unique")
    git(repository, "switch", "main")
    monkeypatch.setattr(cleanup, "load_pull_requests", lambda _repo: {})

    plan = cleanup.build_plan(repository, base="main", remote="origin", protected={"main", "production"})
    actions = {decision.branch: decision.action for decision in plan}

    assert actions == {
        "dirty-worktree": "keep",
        "main": "keep",
        "stale-branch": "delete",
        "stale-worktree": "delete",
        "unique-work": "keep",
    }

    cleanup.apply_plan(repository, plan)

    assert not clean_path.exists()
    assert dirty_path.exists()
    assert git(repository, "branch", "--list", "stale-branch") == ""
    assert git(repository, "branch", "--list", "stale-worktree") == ""
    assert git(repository, "branch", "--list", "dirty-worktree")
    assert git(repository, "branch", "--list", "unique-work")


def commit_on(repo: Path, message: str) -> str:
    """Commit a change to tracked.txt and return the new commit's OID."""

    (repo / "tracked.txt").write_text(f"{message}\n", encoding="utf-8")
    git(repo, "commit", "-qam", message)
    return git(repo, "rev-parse", "HEAD")


def test_detached_worktrees_are_planned_and_only_recoverable_ones_removed(
    repository: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """Detached worktrees at main or a merged PR head go; unique or dirty ones stay."""

    at_main = tmp_path / "at-main"
    at_pr = tmp_path / "at-pr"
    unique = tmp_path / "unique"
    dirty = tmp_path / "dirty"
    git(repository, "switch", "-qc", "pr-branch")
    pr_head = commit_on(repository, "merged pr work")
    git(repository, "switch", "-qc", "scratch", "main")
    unique_head = commit_on(repository, "never pushed")
    git(repository, "switch", "-q", "main")
    git(repository, "branch", "-qD", "pr-branch", "scratch")
    git(repository, "worktree", "add", "-q", "--detach", str(at_main), "main")
    git(repository, "worktree", "add", "-q", "--detach", str(at_pr), pr_head)
    git(repository, "worktree", "add", "-q", "--detach", str(unique), unique_head)
    git(repository, "worktree", "add", "-q", "--detach", str(dirty), "main")
    (dirty / "notes.txt").write_text("do not discard\n", encoding="utf-8")
    merged = cleanup.PullRequest(42, "MERGED", pr_head)
    monkeypatch.setattr(cleanup, "load_pull_requests", lambda _repo: {"gone-branch": (merged,)})

    plan = cleanup.build_plan(repository, base="main", remote="origin", protected={"main", "production"})
    by_path = {d.worktree.path: d for d in plan if d.branch is None and d.worktree is not None}

    assert by_path[at_main.resolve()].action == "delete"
    assert by_path[at_pr.resolve()].action == "delete"
    assert "merged PR #42" in by_path[at_pr.resolve()].reason
    assert by_path[unique.resolve()].action == "keep"
    assert by_path[dirty.resolve()].action == "keep"

    cleanup.apply_plan(repository, plan)

    assert not at_main.exists()
    assert not at_pr.exists()
    assert unique.exists()
    assert dirty.exists()


def test_review_worktrees_survive_their_pr_merging(repository: Path, monkeypatch: pytest.MonkeyPatch):
    """`/review-pr` leaves its worktree for `/follow-up-pr-review`, merged or not."""

    git(repository, "switch", "-qc", "reviewed")
    pr_head = commit_on(repository, "reviewed work")
    git(repository, "switch", "-q", "main")
    git(repository, "branch", "-qD", "reviewed")
    review_path = repository / ".worktrees" / "review-fix" / "item-85"
    git(repository, "worktree", "add", "-q", "--detach", str(review_path), pr_head)
    merged = cleanup.PullRequest(85, "MERGED", pr_head)
    monkeypatch.setattr(cleanup, "load_pull_requests", lambda _repo: {"fix/item-85": (merged,)})

    plan = cleanup.build_plan(repository, base="main", remote="origin", protected={"main", "production"})
    review = next(d for d in plan if d.worktree is not None and d.worktree.path == review_path.resolve())

    assert review.action == "keep"
    assert "review worktree" in review.reason
    cleanup.apply_plan(repository, plan)
    assert review_path.exists()


def test_a_pre_merge_snapshot_is_proven_by_fetching_the_pr_head(
    repository: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """The merged PR's head is fetched from refs/pull/N/head when it is not local yet."""

    git(repository, "switch", "-qc", "feature")
    snapshot = commit_on(repository, "first half")
    git(repository, "switch", "-q", "main")
    git(repository, "push", "-q", "origin", "feature")
    other = tmp_path / "other"
    git(tmp_path, "clone", "-q", str(tmp_path / "remote.git"), str(other))
    git(other, "config", "user.name", "Cleanup Test")
    git(other, "config", "user.email", "cleanup@example.com")
    git(other, "switch", "-q", "feature")
    pr_head = commit_on(other, "second half")
    git(other, "push", "-q", "origin", f"{pr_head}:refs/pull/7/head")
    git(other, "push", "-q", "origin", "--delete", "feature")
    missing = subprocess.run(("git", "-C", str(repository), "cat-file", "-e", pr_head), capture_output=True)
    assert missing.returncode != 0
    merged = cleanup.PullRequest(7, "MERGED", pr_head)
    monkeypatch.setattr(cleanup, "load_pull_requests", lambda _repo: {"feature": (merged,)})

    plan = cleanup.build_plan(repository, base="main", remote="origin", protected={"main", "production"})
    feature = next(d for d in plan if d.branch == "feature")

    assert feature.action == "delete"
    assert "ancestor of merged PR #7" in feature.reason
    assert feature.head == snapshot


def test_a_detached_pre_merge_snapshot_is_proven_after_its_branch_is_gone(
    repository: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """No local branch names the PR, so the detached HEAD pulls in every missing merged head."""

    git(repository, "switch", "-qc", "feature")
    snapshot = commit_on(repository, "first half")
    git(repository, "switch", "-q", "main")
    git(repository, "push", "-q", "origin", "feature")
    other = tmp_path / "other"
    git(tmp_path, "clone", "-q", str(tmp_path / "remote.git"), str(other))
    git(other, "config", "user.name", "Cleanup Test")
    git(other, "config", "user.email", "cleanup@example.com")
    git(other, "switch", "-q", "feature")
    pr_head = commit_on(other, "second half")
    git(other, "push", "-q", "origin", f"{pr_head}:refs/pull/7/head")
    git(other, "push", "-q", "origin", "--delete", "feature")
    git(repository, "branch", "-qD", "feature")
    snapshot_path = tmp_path / "snapshot"
    git(repository, "worktree", "add", "-q", "--detach", str(snapshot_path), snapshot)
    merged = cleanup.PullRequest(7, "MERGED", pr_head)
    monkeypatch.setattr(cleanup, "load_pull_requests", lambda _repo: {"feature": (merged,)})

    plan = cleanup.build_plan(repository, base="main", remote="origin", protected={"main", "production"})
    decision = next(d for d in plan if d.worktree is not None and d.worktree.path == snapshot_path.resolve())

    assert decision.action == "delete"
    assert "ancestor of merged PR #7" in decision.reason
    assert git(repository, "for-each-ref", "refs/pull") == ""


def test_a_changed_tip_is_retained_when_the_pr_head_cannot_be_fetched(
    repository: Path, monkeypatch: pytest.MonkeyPatch
):
    """No refs/pull/N/head on the remote means no evidence, so the branch stays."""

    git(repository, "switch", "-qc", "feature")
    commit_on(repository, "local only")
    git(repository, "switch", "-q", "main")
    merged = cleanup.PullRequest(8, "MERGED", "d" * 40)
    monkeypatch.setattr(cleanup, "load_pull_requests", lambda _repo: {"feature": (merged,)})

    plan = cleanup.build_plan(repository, base="main", remote="origin", protected={"main", "production"})
    feature = next(d for d in plan if d.branch == "feature")

    assert feature.action == "keep"
    assert "local tip differs" in feature.reason


def test_apply_aborts_if_a_worktree_becomes_dirty(repository: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """The apply phase rechecks worktree state after displaying its plan."""

    worktree_path = tmp_path / "raced-tree"
    git(repository, "worktree", "add", "-b", "raced", str(worktree_path), "main")
    monkeypatch.setattr(cleanup, "load_pull_requests", lambda _repo: {})
    plan = cleanup.build_plan(repository, base="main", remote="origin", protected={"main", "production"})
    (worktree_path / "late.txt").write_text("appeared after planning\n", encoding="utf-8")

    with pytest.raises(cleanup.CleanupError, match="became dirty"):
        cleanup.apply_plan(repository, plan)

    assert worktree_path.exists()
    assert git(repository, "branch", "--list", "raced")


def test_apply_aborts_if_a_detached_worktree_moves(repository: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """A detached worktree whose HEAD changed after planning is not removed."""

    worktree_path = tmp_path / "moved-tree"
    git(repository, "worktree", "add", "-q", "--detach", str(worktree_path), "main")
    monkeypatch.setattr(cleanup, "load_pull_requests", lambda _repo: {})
    plan = cleanup.build_plan(repository, base="main", remote="origin", protected={"main", "production"})
    commit_on(worktree_path, "moved after planning")

    with pytest.raises(cleanup.CleanupError, match="changed after planning"):
        cleanup.apply_plan(repository, plan)

    assert worktree_path.exists()


def test_stale_base_is_rejected(repository: Path):
    """Cleanup cannot classify branches against a base older than origin/main."""

    other = repository.parent / "other"
    git(repository.parent, "clone", str(repository.parent / "remote.git"), str(other))
    git(other, "config", "user.name", "Cleanup Test")
    git(other, "config", "user.email", "cleanup@example.com")
    (other / "remote.txt").write_text("new\n", encoding="utf-8")
    git(other, "add", "remote.txt")
    git(other, "commit", "-m", "advance remote")
    git(other, "push", "origin", "main")
    git(repository, "fetch", "origin")

    with pytest.raises(cleanup.CleanupError, match="does not match origin/main"):
        cleanup.validate_base(repository, "main", "origin")
