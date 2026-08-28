#!/usr/bin/env python3
"""Plan or remove local branches and worktrees that are proven safe to delete."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, Sequence


class CleanupError(RuntimeError):
    """Raised when cleanup cannot make a conservative decision."""


@dataclass(frozen=True)
class PullRequest:
    """GitHub PR evidence for one local branch name."""

    number: int
    state: str
    head_oid: str | None


@dataclass(frozen=True)
class Worktree:
    """One entry from ``git worktree list --porcelain``."""

    path: Path
    branch: str | None
    locked: bool = False
    prunable: bool = False


@dataclass(frozen=True)
class BranchFacts:
    """Evidence used to decide whether one branch is disposable."""

    branch: str
    head: str
    protected: bool
    current: bool
    ancestor_of_base: bool
    tree_matches_base: bool
    pull_requests: tuple[PullRequest, ...]
    worktree: Worktree | None = None
    dirty: bool = False


@dataclass(frozen=True)
class Decision:
    """A conservative keep/delete decision for one local branch."""

    branch: str
    head: str
    action: Literal["keep", "delete"]
    reason: str
    worktree: Worktree | None = None


def run_command(args: Sequence[str | Path], *, cwd: Path, check: bool = True) -> subprocess.CompletedProcess[str]:
    """Run a command without a shell and capture its output."""

    result = subprocess.run(tuple(str(arg) for arg in args), cwd=cwd, capture_output=True, text=True, check=False)
    if check and result.returncode != 0:
        detail = result.stderr.strip() or result.stdout.strip() or f"exit {result.returncode}"
        raise CleanupError(f"{' '.join(str(arg) for arg in args[:3])} failed: {detail}")
    return result


def run_git(repo: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    """Run Git against ``repo`` without relying on the caller's directory."""

    return run_command(("git", "-C", repo, *args), cwd=repo, check=check)


def parse_worktrees(output: str) -> list[Worktree]:
    """Parse stable porcelain worktree output without splitting paths on spaces."""

    worktrees: list[Worktree] = []
    for record in filter(None, output.strip().split("\n\n")):
        fields = record.splitlines()
        path_line = next((line for line in fields if line.startswith("worktree ")), None)
        if path_line is None:
            raise CleanupError("git worktree output omitted its path")
        branch_line = next((line for line in fields if line.startswith("branch refs/heads/")), None)
        worktrees.append(
            Worktree(
                path=Path(path_line.removeprefix("worktree ")).resolve(),
                branch=branch_line.removeprefix("branch refs/heads/") if branch_line else None,
                locked=any(line == "locked" or line.startswith("locked ") for line in fields),
                prunable=any(line == "prunable" or line.startswith("prunable ") for line in fields),
            )
        )
    return worktrees


def local_branches(repo: Path) -> dict[str, str]:
    """Return local branch names and full tip OIDs."""

    output = run_git(repo, "for-each-ref", "--format=%(refname:short)\t%(objectname)", "refs/heads").stdout
    return dict(line.split("\t", 1) for line in output.splitlines() if line)


def load_pull_requests(repo: Path) -> dict[str, tuple[PullRequest, ...]]:
    """Load bounded GitHub PR evidence once and group it by exact head branch."""

    result = run_command(
        (
            "gh",
            "pr",
            "list",
            "--state",
            "all",
            "--limit",
            "1000",
            "--json",
            "number,state,headRefName,headRefOid",
        ),
        cwd=repo,
    )
    try:
        payload = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise CleanupError(f"gh pr list returned invalid JSON: {exc}") from exc

    grouped: dict[str, list[PullRequest]] = {}
    for item in payload:
        branch = item.get("headRefName")
        if not isinstance(branch, str):
            continue
        grouped.setdefault(branch, []).append(
            PullRequest(
                number=int(item["number"]),
                state=str(item["state"]).upper(),
                head_oid=item.get("headRefOid") if isinstance(item.get("headRefOid"), str) else None,
            )
        )
    return {branch: tuple(records) for branch, records in grouped.items()}


def decide_branch(facts: BranchFacts) -> Decision:
    """Return a deletion decision based only on explicit recoverability evidence."""

    if facts.protected:
        return Decision(facts.branch, facts.head, "keep", "protected branch", facts.worktree)
    if facts.current:
        return Decision(facts.branch, facts.head, "keep", "checked out in the invoking worktree", facts.worktree)

    open_prs = [pr for pr in facts.pull_requests if pr.state == "OPEN"]
    if open_prs:
        numbers = ", ".join(f"#{pr.number}" for pr in open_prs)
        return Decision(facts.branch, facts.head, "keep", f"open PR {numbers}", facts.worktree)
    if facts.worktree and facts.worktree.locked:
        return Decision(facts.branch, facts.head, "keep", "worktree is locked", facts.worktree)
    if facts.worktree and facts.worktree.prunable:
        return Decision(
            facts.branch, facts.head, "keep", "worktree metadata is prunable; inspect it first", facts.worktree
        )
    if facts.worktree and facts.dirty:
        return Decision(facts.branch, facts.head, "keep", "worktree has uncommitted or untracked files", facts.worktree)

    if facts.ancestor_of_base:
        return Decision(facts.branch, facts.head, "delete", "tip is already an ancestor of the base", facts.worktree)
    if facts.tree_matches_base:
        return Decision(facts.branch, facts.head, "delete", "tip tree is identical to the base", facts.worktree)

    matching_merges = [
        pr
        for pr in facts.pull_requests
        if pr.state == "MERGED" and pr.head_oid is not None and pr.head_oid == facts.head
    ]
    if matching_merges:
        numbers = ", ".join(f"#{pr.number}" for pr in matching_merges)
        return Decision(
            facts.branch,
            facts.head,
            "delete",
            f"tip exactly matches merged PR {numbers}",
            facts.worktree,
        )

    merged_prs = [pr for pr in facts.pull_requests if pr.state == "MERGED"]
    if merged_prs:
        numbers = ", ".join(f"#{pr.number}" for pr in merged_prs)
        return Decision(
            facts.branch,
            facts.head,
            "keep",
            f"merged PR {numbers} exists, but local tip differs (possible post-merge commits)",
            facts.worktree,
        )
    closed_prs = [pr for pr in facts.pull_requests if pr.state == "CLOSED"]
    if closed_prs:
        numbers = ", ".join(f"#{pr.number}" for pr in closed_prs)
        return Decision(facts.branch, facts.head, "keep", f"only unmerged closed PR {numbers} exists", facts.worktree)
    return Decision(facts.branch, facts.head, "keep", "unique local work with no merged PR evidence", facts.worktree)


def is_ancestor(repo: Path, branch: str, base: str) -> bool:
    """Return whether ``branch`` is already contained in ``base``."""

    result = run_git(
        repo,
        "merge-base",
        "--is-ancestor",
        f"refs/heads/{branch}",
        f"refs/heads/{base}",
        check=False,
    )
    if result.returncode not in (0, 1):
        detail = result.stderr.strip() or f"exit {result.returncode}"
        raise CleanupError(f"could not compare {branch} with {base}: {detail}")
    return result.returncode == 0


def worktree_is_dirty(repo: Path, worktree: Worktree) -> bool:
    """Treat staged, unstaged, and untracked files as non-discardable state."""

    return bool(run_git(repo, "-C", str(worktree.path), "status", "--porcelain").stdout)


def validate_base(repo: Path, base: str, remote: str) -> None:
    """Refuse cleanup until the local base exactly matches the fetched remote base."""

    base_head = run_git(repo, "rev-parse", "--verify", f"refs/heads/{base}").stdout.strip()
    remote_head = run_git(repo, "rev-parse", "--verify", f"refs/remotes/{remote}/{base}").stdout.strip()
    if base_head != remote_head:
        raise CleanupError(
            f"{base} ({base_head[:12]}) does not match {remote}/{base} ({remote_head[:12]}); "
            f"fetch and fast-forward {base} before cleanup"
        )


def build_plan(repo: Path, *, base: str, remote: str, protected: set[str]) -> list[Decision]:
    """Gather Git/GitHub evidence and return a complete local cleanup plan."""

    validate_base(repo, base, remote)
    branches = local_branches(repo)
    if base not in branches:
        raise CleanupError(f"local base branch {base!r} does not exist")

    current_path = Path(run_git(repo, "rev-parse", "--show-toplevel").stdout.strip()).resolve()
    worktrees = parse_worktrees(run_git(repo, "worktree", "list", "--porcelain").stdout)
    by_branch = {worktree.branch: worktree for worktree in worktrees if worktree.branch is not None}
    pull_requests = load_pull_requests(repo)
    base_tree = run_git(repo, "rev-parse", f"refs/heads/{base}^{{tree}}").stdout.strip()

    decisions: list[Decision] = []
    for branch, head in sorted(branches.items()):
        worktree = by_branch.get(branch)
        branch_tree = run_git(repo, "rev-parse", f"refs/heads/{branch}^{{tree}}").stdout.strip()
        decisions.append(
            decide_branch(
                BranchFacts(
                    branch=branch,
                    head=head,
                    protected=branch in protected,
                    current=worktree is not None and worktree.path == current_path,
                    ancestor_of_base=is_ancestor(repo, branch, base),
                    tree_matches_base=branch_tree == base_tree,
                    pull_requests=pull_requests.get(branch, ()),
                    worktree=worktree,
                    dirty=worktree_is_dirty(repo, worktree)
                    if worktree is not None and worktree.path.exists() and not worktree.prunable
                    else False,
                )
            )
        )
    return decisions


def apply_plan(repo: Path, decisions: list[Decision]) -> None:
    """Delete planned local state, rechecking mutable facts immediately beforehand."""

    invoking_path = Path(run_git(repo, "rev-parse", "--show-toplevel").stdout.strip()).resolve()
    for decision in decisions:
        if decision.action != "delete":
            continue
        current_head = run_git(repo, "rev-parse", "--verify", f"refs/heads/{decision.branch}").stdout.strip()
        if current_head != decision.head:
            raise CleanupError(f"{decision.branch} moved after planning; rerun cleanup")
        if decision.worktree is not None:
            if decision.worktree.path == invoking_path:
                raise CleanupError(f"refusing to remove the invoking worktree: {invoking_path}")
            current_worktrees = parse_worktrees(run_git(repo, "worktree", "list", "--porcelain").stdout)
            current = next((item for item in current_worktrees if item.branch == decision.branch), None)
            if current is None or current.path != decision.worktree.path or current.locked or current.prunable:
                raise CleanupError(f"{decision.branch} worktree changed after planning; rerun cleanup")
            if worktree_is_dirty(repo, current):
                raise CleanupError(f"{decision.branch} became dirty after planning; this worktree was not removed")
            run_git(repo, "worktree", "remove", str(current.path))
        run_git(repo, "branch", "-D", "--", decision.branch)
        suffix = f" and {decision.worktree.path}" if decision.worktree else ""
        print(f"DELETED {decision.branch}{suffix}")
    run_git(repo, "worktree", "prune")


def print_plan(decisions: list[Decision], *, applying: bool) -> None:
    """Print every keep/delete decision so the safety boundary is inspectable."""

    for decision in decisions:
        location = f" worktree={decision.worktree.path}" if decision.worktree else ""
        print(f"{decision.action.upper():6} {decision.branch} ({decision.head[:12]}){location} — {decision.reason}")
    delete_count = sum(decision.action == "delete" for decision in decisions)
    print(f"Summary: {delete_count} deletable, {len(decisions) - delete_count} retained")
    if delete_count and not applying:
        print("Dry run only. Re-run with --apply to remove the listed local refs/worktrees.")


def main() -> int:
    """CLI entry point."""

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, default=Path.cwd(), help="Any worktree in the target repository")
    parser.add_argument("--base", default="main", help="Up-to-date integration branch (default: main)")
    parser.add_argument("--remote", default="origin", help="Fetched remote used to verify the base (default: origin)")
    parser.add_argument("--protect", action="append", default=[], metavar="BRANCH", help="Additional branch to retain")
    parser.add_argument("--apply", action="store_true", help="Apply the printed plan; default is a dry run")
    args = parser.parse_args()

    repo = args.repo.resolve()
    protected = {args.base, "production", *args.protect}
    try:
        repo = Path(run_git(repo, "rev-parse", "--show-toplevel").stdout.strip()).resolve()
        decisions = build_plan(repo, base=args.base, remote=args.remote, protected=protected)
        print_plan(decisions, applying=args.apply)
        if args.apply:
            apply_plan(repo, decisions)
    except (CleanupError, OSError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
