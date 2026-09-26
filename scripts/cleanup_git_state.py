#!/usr/bin/env python3
"""Plan or remove local branches and worktrees that are proven safe to delete.

A tip is proven safe only when its commits stay recoverable after deletion: it is
already in the base, its tree equals the base, or it is the head (or an ancestor of
the head) of a merged PR, whose ``refs/pull/N/head`` GitHub keeps permanently.
"""

from __future__ import annotations

import argparse
import itertools
import json
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Container, Iterable, Literal, Sequence

# `/review-pr`'s own `git worktree add .worktrees/review-<headRefName>` convention
# (`.claude/commands/review-pr.md`). Review worktrees are left in place by design:
# `/follow-up-pr-review` diffs the next push against the head they hold. If that
# command ever renames either part, the match silently stops firing -- named here so
# the coupling is greppable from both ends, and covered by
# `test_review_worktree_naming_matches_review_pr_md`.
REVIEW_WORKTREE_PARENT_DIR = ".worktrees"
REVIEW_WORKTREE_PREFIX = "review-"


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
    head: str | None = None


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
    head_prs: tuple[PullRequest, ...] = ()
    """PRs of any branch name whose head is exactly this tip."""
    ancestor_of_merged: tuple[int, ...] = ()
    """Merged PRs whose head descends from this tip."""


@dataclass(frozen=True)
class DetachedFacts:
    """Evidence used to decide whether one detached-HEAD worktree is disposable."""

    worktree: Worktree
    primary: bool
    current: bool
    dirty: bool
    review: bool
    """The worktree follows `/review-pr`'s naming, which keeps it on purpose."""
    ancestor_of_base: bool
    tree_matches_base: bool
    head_prs: tuple[PullRequest, ...] = ()
    ancestor_of_merged: tuple[int, ...] = ()


@dataclass(frozen=True)
class Decision:
    """A conservative keep/delete decision for one local branch or detached worktree."""

    branch: str | None
    head: str
    action: Literal["keep", "delete"]
    reason: str
    worktree: Worktree | None = None


def run_command(
    args: Sequence[str | Path], *, cwd: Path, check: bool = True, stdin: str | None = None
) -> subprocess.CompletedProcess[str]:
    """Run a command without a shell and capture its output."""

    result = subprocess.run(
        tuple(str(arg) for arg in args), cwd=cwd, input=stdin, capture_output=True, text=True, check=False
    )
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
        head_line = next((line for line in fields if line.startswith("HEAD ")), None)
        worktrees.append(
            Worktree(
                path=Path(path_line.removeprefix("worktree ")).resolve(),
                branch=branch_line.removeprefix("branch refs/heads/") if branch_line else None,
                locked=any(line == "locked" or line.startswith("locked ") for line in fields),
                prunable=any(line == "prunable" or line.startswith("prunable ") for line in fields),
                head=head_line.removeprefix("HEAD ") if head_line else None,
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

    # An open PR under this branch's name may still receive local work, so it always
    # protects the branch. One matched only by head protects commits the base does
    # not have, and any worktree a reviewer of that PR may be standing in; a bare ref
    # whose tip is already in the base loses nothing when it goes.
    in_base = facts.ancestor_of_base or facts.tree_matches_base
    head_matched = facts.head_prs if facts.worktree is not None or not in_base else ()
    open_prs = open_pull_requests((*facts.pull_requests, *head_matched))
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

    matching_merges = merged_heads_equal((*facts.pull_requests, *facts.head_prs), facts.head)
    if matching_merges:
        return Decision(
            facts.branch,
            facts.head,
            "delete",
            f"tip exactly matches merged PR {matching_merges}",
            facts.worktree,
        )
    if facts.ancestor_of_merged:
        numbers = ", ".join(f"#{number}" for number in facts.ancestor_of_merged)
        return Decision(
            facts.branch,
            facts.head,
            "delete",
            f"tip is an ancestor of merged PR {numbers}'s head (a pre-merge snapshot)",
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


def open_pull_requests(prs: Sequence[PullRequest]) -> list[PullRequest]:
    """Open PRs, deduplicated by number (one PR can match by name and by head)."""

    return sorted({pr.number: pr for pr in prs if pr.state == "OPEN"}.values(), key=lambda pr: pr.number)


def merged_heads_equal(prs: Sequence[PullRequest], head: str) -> str:
    """Comma-separated numbers of merged PRs whose head is exactly ``head``."""

    numbers = sorted({pr.number for pr in prs if pr.state == "MERGED" and pr.head_oid == head})
    return ", ".join(f"#{number}" for number in numbers)


def decide_detached(facts: DetachedFacts) -> Decision:
    """Decide a worktree that has no branch, on the evidence a branch would need.

    Stricter than ``decide_branch`` in two ways, because a worktree is a working
    area rather than a ref: review worktrees are always kept, and an open PR headed
    at this commit keeps it even when the commit is already in the base -- someone
    reviewing that PR (a release PR headed at main, say) may be standing in it.
    """

    worktree = facts.worktree
    head = worktree.head or ""

    def keep(reason: str) -> Decision:
        return Decision(None, head, "keep", reason, worktree)

    def delete(reason: str) -> Decision:
        return Decision(None, head, "delete", reason, worktree)

    if facts.primary:
        return keep("primary worktree")
    if facts.current:
        return keep("the invoking worktree")
    if facts.review:
        return keep("review worktree; /follow-up-pr-review diffs against the head it holds")
    open_prs = open_pull_requests(facts.head_prs)
    if open_prs:
        return keep(f"open PR {', '.join(f'#{pr.number}' for pr in open_prs)} is headed here")
    if worktree.locked:
        return keep("worktree is locked")
    if worktree.prunable or not worktree.head:
        return keep("worktree metadata is prunable or incomplete; inspect it first")
    if facts.dirty:
        return keep("worktree has uncommitted or untracked files")
    if facts.ancestor_of_base:
        return delete("detached HEAD is an ancestor of the base")
    if facts.tree_matches_base:
        return delete("detached HEAD's tree is identical to the base")
    matching_merges = merged_heads_equal(facts.head_prs, head)
    if matching_merges:
        return delete(f"detached HEAD exactly matches merged PR {matching_merges}")
    if facts.ancestor_of_merged:
        numbers = ", ".join(f"#{number}" for number in facts.ancestor_of_merged)
        return delete(f"detached HEAD is an ancestor of merged PR {numbers}'s head")
    return keep("detached HEAD with no recoverability evidence")


def is_review_worktree(worktree_path: Path) -> bool:
    """Match `/review-pr`'s own worktree naming shape.

    Matched structurally: a `REVIEW_WORKTREE_PARENT_DIR` segment immediately
    followed by a `REVIEW_WORKTREE_PREFIX`-prefixed one, and nothing shaped
    like a further nested worktree after that pair. Not just the last two
    components, since `/review-pr` names the review worktree after the
    reviewed branch's own head ref, which can itself contain slashes
    (`fix/item-85` produces `.worktrees/review-fix/item-85`, three
    components deep, not two) -- and not anchored to any particular
    checkout, since `/review-pr` runs its `git worktree add` relative to
    whichever checkout invokes it, normally but not necessarily the main
    one. The "nothing nested after" requirement excludes a worktree created
    *inside* a review worktree (e.g. an agent worktree somehow created
    from one) from inheriting this exemption.
    """

    parts = worktree_path.parts
    for index, (parent, child) in enumerate(itertools.pairwise(parts)):
        if parent == REVIEW_WORKTREE_PARENT_DIR and child.startswith(REVIEW_WORKTREE_PREFIX):
            return not any(part in {"worktrees", REVIEW_WORKTREE_PARENT_DIR} for part in parts[index + 2 :])
    return False


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


def is_ancestor_commit(repo: Path, commit: str, descendant: str) -> bool:
    """Return whether ``commit`` is reachable from ``descendant`` (both OIDs or refs)."""

    result = run_git(repo, "merge-base", "--is-ancestor", commit, descendant, check=False)
    if result.returncode not in (0, 1):
        detail = result.stderr.strip() or f"exit {result.returncode}"
        raise CleanupError(f"could not compare {commit[:12]} with {descendant[:12]}: {detail}")
    return result.returncode == 0


def present_commits(repo: Path, oids: Sequence[str]) -> set[str]:
    """The subset of ``oids`` whose commit objects exist locally."""

    if not oids:
        return set()
    result = run_command(
        ("git", "-C", repo, "cat-file", "--batch-check"), cwd=repo, check=False, stdin="\n".join(oids) + "\n"
    )
    present = set()
    for line in result.stdout.splitlines():
        parts = line.split()
        if len(parts) >= 2 and parts[1] == "commit":
            present.add(parts[0])
    return present


def fetch_pull_heads(repo: Path, remote: str, numbers: Iterable[int]) -> None:
    """Fetch PRs' permanent head refs into the object store only, in one round trip.

    Writes no ref and no FETCH_HEAD, so another session's state is untouched. A
    failure (e.g. a remote that is not GitHub) just leaves the evidence missing.
    """

    refspecs = "".join(f"refs/pull/{number}/head\n" for number in sorted(set(numbers)))
    # With no refspec, fetch would fall back to the remote's configured ones and
    # move remote-tracking refs.
    if not refspecs:
        return
    run_command(
        ("git", "-C", repo, "fetch", "--quiet", "--no-tags", "--no-write-fetch-head", "--stdin", remote),
        cwd=repo,
        check=False,
        stdin=refspecs,
    )


def needs_merged_evidence(repo: Path, worktree: Worktree, base: str, merged_heads: Container[str]) -> bool:
    """Whether a detached worktree could only be proven disposable as a pre-merge snapshot."""

    return (
        worktree.branch is None
        and worktree.head is not None
        and worktree.head not in merged_heads
        and worktree.path.exists()
        and not (worktree.locked or worktree.prunable)
        and not is_review_worktree(worktree.path)
        and not is_ancestor_commit(repo, worktree.head, f"refs/heads/{base}")
    )


class MergedPullHeads:
    """Answers "which merged PR heads descend from this commit?" cheaply.

    One ``rev-list`` collects every commit reachable from a locally present
    merged head but not from the base; only candidates in that set pay for a
    per-head ancestry check to name the PRs.
    """

    def __init__(self, repo: Path, base: str, heads: dict[str, tuple[int, ...]]):
        self.repo = repo
        self.heads = heads
        if heads:
            result = run_command(
                ("git", "-C", repo, "rev-list", "--stdin"),
                cwd=repo,
                stdin="\n".join((*heads, f"^refs/heads/{base}")) + "\n",
            )
            self.reachable = set(result.stdout.split())
        else:
            self.reachable = set()

    def descendants_of(self, commit: str) -> tuple[int, ...]:
        if commit not in self.reachable:
            return ()
        numbers = {n for head, prs in self.heads.items() if is_ancestor_commit(self.repo, commit, head) for n in prs}
        return tuple(sorted(numbers))


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

    by_head: dict[str, list[PullRequest]] = {}
    for named in pull_requests.values():
        for pr in named:
            if pr.head_oid:
                by_head.setdefault(pr.head_oid, []).append(pr)
    merged_numbers: dict[str, set[int]] = {}
    for oid, headed in by_head.items():
        for pr in headed:
            if pr.state == "MERGED":
                merged_numbers.setdefault(oid, set()).add(pr.number)
    # A merged PR head that differs from a local tip may be a later state of it;
    # fetch missing heads so ancestry can prove a pre-merge snapshot. A branch
    # names its candidate PRs; a detached HEAD names none, so it needs them all.
    present = present_commits(repo, sorted(merged_numbers))
    missing = {oid: numbers for oid, numbers in merged_numbers.items() if oid not in present}
    wanted = {
        pr.number
        for branch, head in branches.items()
        for pr in pull_requests.get(branch, ())
        if pr.state == "MERGED" and pr.head_oid in missing and pr.head_oid != head
    }
    if missing and any(needs_merged_evidence(repo, worktree, base, merged_numbers) for worktree in worktrees):
        wanted.update(number for numbers in missing.values() for number in numbers)
    if wanted:
        fetch_pull_heads(repo, remote, wanted)
        present = present_commits(repo, sorted(merged_numbers))
    merged_heads = MergedPullHeads(
        repo, base, {oid: tuple(sorted(numbers)) for oid, numbers in merged_numbers.items() if oid in present}
    )

    def tree_of(rev: str) -> str:
        return run_git(repo, "rev-parse", f"{rev}^{{tree}}").stdout.strip()

    def is_dirty(worktree: Worktree | None) -> bool:
        return (
            worktree is not None
            and worktree.path.exists()
            and not worktree.prunable
            and worktree_is_dirty(repo, worktree)
        )

    decisions: list[Decision] = []
    for branch, head in sorted(branches.items()):
        worktree = by_branch.get(branch)
        decisions.append(
            decide_branch(
                BranchFacts(
                    branch=branch,
                    head=head,
                    protected=branch in protected,
                    current=worktree is not None and worktree.path == current_path,
                    ancestor_of_base=is_ancestor(repo, branch, base),
                    tree_matches_base=tree_of(f"refs/heads/{branch}") == base_tree,
                    pull_requests=pull_requests.get(branch, ()),
                    worktree=worktree,
                    dirty=is_dirty(worktree),
                    head_prs=tuple(by_head.get(head, ())),
                    ancestor_of_merged=merged_heads.descendants_of(head),
                )
            )
        )

    primary_path = worktrees[0].path if worktrees else None
    for worktree in worktrees:
        if worktree.branch is not None:
            continue
        detached_head = worktree.head
        # Missing or prunable worktrees carry no usable HEAD; decide_detached keeps them.
        inspectable = detached_head is not None and worktree.path.exists() and not worktree.prunable
        decisions.append(
            decide_detached(
                DetachedFacts(
                    worktree=worktree,
                    primary=worktree.path == primary_path,
                    current=worktree.path == current_path,
                    dirty=is_dirty(worktree),
                    review=is_review_worktree(worktree.path),
                    ancestor_of_base=detached_head is not None
                    and inspectable
                    and is_ancestor_commit(repo, detached_head, f"refs/heads/{base}"),
                    tree_matches_base=detached_head is not None and inspectable and tree_of(detached_head) == base_tree,
                    head_prs=tuple(by_head.get(detached_head, ())) if detached_head else (),
                    ancestor_of_merged=merged_heads.descendants_of(detached_head)
                    if detached_head is not None and inspectable
                    else (),
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
        if decision.branch is None:
            remove_detached_worktree(repo, decision, invoking_path)
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


def remove_detached_worktree(repo: Path, decision: Decision, invoking_path: Path) -> None:
    """Remove one planned detached worktree after rechecking it is unchanged and clean."""

    planned = decision.worktree
    if planned is None:
        raise CleanupError("detached decision without a worktree")
    if planned.path == invoking_path:
        raise CleanupError(f"refusing to remove the invoking worktree: {invoking_path}")
    current_worktrees = parse_worktrees(run_git(repo, "worktree", "list", "--porcelain").stdout)
    current = next((item for item in current_worktrees if item.path == planned.path), None)
    if (
        current is None
        or current.branch is not None
        or current.head != decision.head
        or current.locked
        or current.prunable
    ):
        raise CleanupError(f"detached worktree {planned.path} changed after planning; rerun cleanup")
    if worktree_is_dirty(repo, current):
        raise CleanupError(f"detached worktree {planned.path} became dirty after planning; it was not removed")
    run_git(repo, "worktree", "remove", str(current.path))
    print(f"DELETED detached worktree {current.path}")


def print_plan(decisions: list[Decision], *, applying: bool) -> None:
    """Print every keep/delete decision so the safety boundary is inspectable."""

    for decision in decisions:
        location = f" worktree={decision.worktree.path}" if decision.worktree else ""
        name = decision.branch if decision.branch is not None else "(detached)"
        print(f"{decision.action.upper():6} {name} ({decision.head[:12]}){location} — {decision.reason}")
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
