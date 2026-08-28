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


def test_parse_worktrees_preserves_paths_and_safety_flags(tmp_path: Path):
    """Porcelain parsing keeps paths with spaces and lock/prune metadata."""

    path = tmp_path / "tree with spaces"
    output = (
        f"worktree {path}\nHEAD {'a' * 40}\nbranch refs/heads/feature\nlocked agent running\n\n"
        f"worktree {tmp_path / 'gone'}\nHEAD {'b' * 40}\ndetached\n"
        "prunable gitdir file points to non-existent location\n"
    )

    worktrees = cleanup.parse_worktrees(output)

    assert worktrees[0] == cleanup.Worktree(path.resolve(), "feature", locked=True)
    assert worktrees[1].branch is None
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
