#!/usr/bin/env python3
"""Plan or apply daily git hygiene: local, remote, and backup-branch cleanup.

This closes three gaps `/vps-loop-run` only handles reactively:

1. Local branch/worktree cleanup (`scripts/cleanup_git_state.py`) currently
   only runs as a side effect of a loop tick, so it never runs during a long
   idle stretch or while the loop is stuck.
2. Merged `vps-loop/item-<N>` branches on GitHub are never deleted by the
   loop itself (accepted operational debt, batched by a human "when it's
   worth the time").
3. `vps-loop/item-<N>-superseded-<sha>` backup branches (created by the
   loop's Step 2b before a judgment-based delete) have no automated prune
   path at all.

This script must never race a live `/vps-loop-run` tick: it acquires the
same `/tmp/claude-loop.lock` lock file non-blockingly before touching
anything and skips cleanly (not an error) if the loop is mid-tick.

Every deletion (local or remote) is appended to a dedicated hygiene log
(default `/root/git-hygiene.log`) -- not `docs/refactor-log.md`, which is
`/vps-loop-run`'s own per-item narrative trail, not a general housekeeping
log.

Like `cleanup_git_state.py`, planning is the default; pass `--apply` to
actually delete anything.
"""

from __future__ import annotations

import argparse
import contextlib
import fcntl
import importlib.util
import io
import json
import re
import sys
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import IO, Literal, Sequence

# Import cleanup_git_state.py directly (not a shell-out) so this script
# reuses its exact, already-reviewed planning/apply safety logic for local
# branches/worktrees rather than duplicating it. Loaded by file path since
# `scripts/` has no `__init__.py` and isn't guaranteed to be on `sys.path`.
_SCRIPT_DIR = Path(__file__).resolve().parent
_CLEANUP_SPEC = importlib.util.spec_from_file_location("cleanup_git_state", _SCRIPT_DIR / "cleanup_git_state.py")
assert _CLEANUP_SPEC and _CLEANUP_SPEC.loader
cleanup_git_state = importlib.util.module_from_spec(_CLEANUP_SPEC)
sys.modules[_CLEANUP_SPEC.name] = cleanup_git_state
_CLEANUP_SPEC.loader.exec_module(cleanup_git_state)

PullRequest = cleanup_git_state.PullRequest
CleanupError = cleanup_git_state.CleanupError

DEFAULT_LOCK_FILE = Path("/tmp/claude-loop.lock")
DEFAULT_LOG_FILE = Path("/root/git-hygiene.log")
DEFAULT_RETENTION_DAYS = 30

# `vps-loop/item-<N>` (no suffix): the branch shape the loop pushes and opens
# PRs from. `vps-loop/item-<N>-superseded-<sha>`: Step 2b's own local-only
# backup-ref convention, deliberately not `cleanup_git_state.py`-managed.
VPS_LOOP_ITEM_BRANCH_RE = re.compile(r"^vps-loop/item-\d+$")
SUPERSEDED_BRANCH_RE = re.compile(r"^vps-loop/item-\d+-superseded-[0-9a-f]+$")


class HygieneError(RuntimeError):
    """Raised when the hygiene job cannot make a conservative decision."""


@dataclass(frozen=True)
class RemoteBranchDecision:
    """A keep/delete decision for one remote `vps-loop/item-<N>` branch."""

    branch: str
    head: str
    action: Literal["keep", "delete"]
    reason: str


@dataclass(frozen=True)
class BackupBranchDecision:
    """A keep/delete decision for one local `...-superseded-<sha>` branch."""

    branch: str
    action: Literal["keep", "delete"]
    reason: str
    commit_epoch: int


# ---------------------------------------------------------------------------
# Concurrency guard
# ---------------------------------------------------------------------------


def try_acquire_lock(lock_path: Path) -> IO[str] | None:
    """Return an open, exclusively-locked file handle, or None if held elsewhere.

    Non-blocking (`LOCK_EX | LOCK_NB`) on the same lock file
    `/root/claude-loop.sh` itself uses to guard against overlapping ticks --
    this job must skip cleanly rather than wait or retry for a live
    `/vps-loop-run` tick to finish.
    """

    lock_path.parent.mkdir(parents=True, exist_ok=True)
    handle = lock_path.open("a+")
    try:
        fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        handle.close()
        return None
    return handle


def release_lock(handle: IO[str]) -> None:
    """Release and close a lock handle obtained from `try_acquire_lock`."""

    fcntl.flock(handle, fcntl.LOCK_UN)
    handle.close()


# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------


def log_line(log_file: Path, message: str) -> None:
    """Append one UTC-timestamped line to the hygiene log."""

    timestamp = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    log_file.parent.mkdir(parents=True, exist_ok=True)
    with log_file.open("a", encoding="utf-8") as handle:
        handle.write(f"{timestamp} {message}\n")


# ---------------------------------------------------------------------------
# 1. Local branch/worktree cleanup -- delegates to cleanup_git_state.py
# ---------------------------------------------------------------------------


def run_local_cleanup(repo: Path, *, base: str, remote: str, protected: set[str], apply: bool, log_file: Path) -> None:
    """Plan (and optionally apply) `cleanup_git_state`'s local cleanup, logging deletes.

    Fetches `remote` first: `build_plan`'s `validate_base` requires local
    `refs/heads/{base}` to exactly match `refs/remotes/{remote}/{base}`, and
    this job is specifically meant to run during idle stretches where nothing
    else has refreshed that remote-tracking ref recently.
    """

    print("== Local branch/worktree cleanup (cleanup_git_state) ==")
    cleanup_git_state.run_git(repo, "fetch", "--prune", remote)
    decisions = cleanup_git_state.build_plan(repo, base=base, remote=remote, protected=protected)
    cleanup_git_state.print_plan(decisions, applying=apply)
    if not apply:
        return

    # Capture apply_plan's own "DELETED ..." lines so the hygiene log records
    # exactly what it actually removed (post its own immediate re-check),
    # not merely what was planned -- and keep echoing them to real stdout.
    buffer = io.StringIO()
    try:
        with contextlib.redirect_stdout(buffer):
            cleanup_git_state.apply_plan(repo, decisions)
    finally:
        output = buffer.getvalue()
        sys.stdout.write(output)
        for line in output.splitlines():
            if line.startswith("DELETED "):
                log_line(log_file, f"local cleanup: {line}")


# ---------------------------------------------------------------------------
# 2. Remote vps-loop/item-* branch cleanup
# ---------------------------------------------------------------------------


def list_remote_vps_loop_branches(repo: Path, remote: str) -> dict[str, str]:
    """Return `vps-loop/item-<N>` branch names on `remote`, mapped to their tip SHA."""

    output = cleanup_git_state.run_command(("git", "ls-remote", "--heads", remote, "vps-loop/item-*"), cwd=repo).stdout
    branches: dict[str, str] = {}
    for line in output.splitlines():
        if not line.strip():
            continue
        sha, ref = line.split("\t", 1)
        name = ref.removeprefix("refs/heads/")
        if VPS_LOOP_ITEM_BRANCH_RE.match(name):
            branches[name] = sha
    return dict(sorted(branches.items()))


def load_dependent_open_prs(repo: Path) -> dict[str, tuple[int, ...]]:
    """Return open PR numbers grouped by their exact base branch name.

    Fetches every open PR once and groups locally by `baseRefName`, the same
    exact-match-in-Python pattern `cleanup_git_state.load_pull_requests` uses
    for `headRefName` -- `gh`'s own `--base <branch>` filter is a search
    qualifier, not guaranteed exact-equality matching, so trusting it alone
    could produce a false negative that lets a real dependent branch be
    deleted out from under an open PR.
    """

    result = cleanup_git_state.run_command(
        ("gh", "pr", "list", "--state", "open", "--limit", "1000", "--json", "number,baseRefName"), cwd=repo
    )
    try:
        payload = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise HygieneError(f"gh pr list --state open returned invalid JSON: {exc}") from exc

    grouped: dict[str, list[int]] = {}
    for item in payload:
        base = item.get("baseRefName")
        if not isinstance(base, str):
            continue
        grouped.setdefault(base, []).append(int(item["number"]))
    return {base: tuple(sorted(numbers)) for base, numbers in grouped.items()}


def decide_remote_branch(
    branch: str, head: str, pull_requests: tuple[PullRequest, ...], dependent_open_prs: tuple[int, ...]
) -> RemoteBranchDecision:
    """Decide whether one remote `vps-loop/item-<N>` branch is safe to delete.

    Deletable only when its remote tip exactly matches a merged PR's head
    commit and no open PR still bases off it -- mirroring
    `cleanup_git_state.decide_branch`'s exact-tip-match requirement for local
    branches (a merged PR whose head no longer matches the branch's current
    tip means real commits landed after the merge, which is not provably
    recoverable). A branch with no PR history at all (a stray manually-pushed
    branch) or only an open/closed-unmerged PR is a human's judgment call,
    not this job's.
    """

    own_open = [pr for pr in pull_requests if pr.state == "OPEN"]
    if own_open:
        numbers = ", ".join(f"#{pr.number}" for pr in own_open)
        return RemoteBranchDecision(branch, head, "keep", f"branch has its own open PR {numbers}")

    merged = [pr for pr in pull_requests if pr.state == "MERGED"]
    if not merged:
        return RemoteBranchDecision(branch, head, "keep", "no merged PR evidence for this branch")

    matching_merges = [pr for pr in merged if pr.head_oid == head]
    if not matching_merges:
        numbers = ", ".join(f"#{pr.number}" for pr in merged)
        return RemoteBranchDecision(
            branch, head, "keep", f"merged PR {numbers} exists, but remote tip differs (possible post-merge commits)"
        )

    if dependent_open_prs:
        numbers = ", ".join(f"#{number}" for number in dependent_open_prs)
        return RemoteBranchDecision(branch, head, "keep", f"open PR {numbers} still bases off this branch")

    numbers = ", ".join(f"#{pr.number}" for pr in matching_merges)
    return RemoteBranchDecision(branch, head, "delete", f"remote tip exactly matches merged PR {numbers}")


def remote_branch_head(repo: Path, remote: str, branch: str) -> str | None:
    """Return one branch's current tip SHA on `remote`, or None if it no longer exists.

    Queried by exact branch name (not the `vps-loop/item-*` glob) so this can be
    used as a cheap, single-branch immediately-before-delete recheck.
    """

    output = cleanup_git_state.run_command(("git", "ls-remote", "--heads", remote, branch), cwd=repo).stdout
    for line in output.splitlines():
        if not line.strip():
            continue
        sha, ref = line.split("\t", 1)
        if ref.removeprefix("refs/heads/") == branch:
            return sha
    return None


def delete_remote_branch(repo: Path, remote: str, branch: str) -> None:
    """Delete one branch on `remote`."""

    cleanup_git_state.run_git(repo, "push", remote, "--delete", "--", branch)


def run_remote_branch_cleanup(repo: Path, *, remote: str, apply: bool, log_file: Path) -> None:
    """Plan (and optionally apply) deletion of merged, non-stacked `vps-loop/item-*` branches."""

    print(f"== Remote vps-loop/item-* branch cleanup ({remote}) ==")
    branches = list_remote_vps_loop_branches(repo, remote)
    pull_requests = cleanup_git_state.load_pull_requests(repo)
    dependents = load_dependent_open_prs(repo)

    decisions = [
        decide_remote_branch(branch, head, pull_requests.get(branch, ()), dependents.get(branch, ()))
        for branch, head in branches.items()
    ]
    for decision in decisions:
        print(f"{decision.action.upper():6} {remote}/{decision.branch} — {decision.reason}")
    delete_count = sum(decision.action == "delete" for decision in decisions)
    print(f"Summary: {delete_count} deletable, {len(decisions) - delete_count} retained")
    if delete_count and not apply:
        print(f"Dry run only. Re-run with --apply to delete the listed {remote} branches.")
    if not apply:
        return

    # Re-fetch dependent-PR evidence once right before applying (not once per
    # branch) to catch a stacked PR opened during planning, without regressing
    # back to one `gh` call per branch.
    fresh_dependents = load_dependent_open_prs(repo)
    for decision in decisions:
        if decision.action != "delete":
            continue
        recheck = fresh_dependents.get(decision.branch, ())
        if recheck:
            numbers = ", ".join(f"#{number}" for number in recheck)
            message = f"remote cleanup: SKIPPED {remote}/{decision.branch} — open PR {numbers} appeared since planning"
            print(message)
            log_line(log_file, message)
            continue
        try:
            current_head = remote_branch_head(repo, remote, decision.branch)
        except (CleanupError, OSError) as exc:
            message = f"remote cleanup: ERROR checking {remote}/{decision.branch} tip: {exc}"
            print(message, file=sys.stderr)
            log_line(log_file, message)
            continue
        if current_head != decision.head:
            observed = current_head[:12] if current_head else "branch gone"
            message = (
                f"remote cleanup: SKIPPED {remote}/{decision.branch} — tip changed since planning "
                f"(planned {decision.head[:12]}, now {observed})"
            )
            print(message)
            log_line(log_file, message)
            continue
        try:
            delete_remote_branch(repo, remote, decision.branch)
        except (CleanupError, OSError) as exc:
            message = f"remote cleanup: ERROR deleting {remote}/{decision.branch}: {exc}"
            print(message, file=sys.stderr)
            log_line(log_file, message)
            continue
        print(f"DELETED {remote}/{decision.branch}")
        log_line(log_file, f"remote cleanup: DELETED {remote}/{decision.branch} — {decision.reason}")


# ---------------------------------------------------------------------------
# 3. Stale superseded-backup branch pruning
# ---------------------------------------------------------------------------


def load_local_superseded_branches(repo: Path) -> dict[str, int]:
    """Return local `vps-loop/item-<N>-superseded-<sha>` branches mapped to their
    backing commit's timestamp, as a unix epoch.

    One bulk `git for-each-ref` call, mirroring the same bulk-read idiom
    `cleanup_git_state.local_branches` already uses for the analogous local-branch
    listing, instead of one `git log` subprocess per branch. A branch-creation
    timestamp isn't directly available from a bare ref, so this uses the commit's
    own recorded committer time instead. Being a single atomic read, it also has no
    per-branch window in which a concurrently-deleted branch could abort the whole
    listing -- unlike a per-branch loop, where one vanished branch would raise and
    stop every subsequent one from being read.
    """

    output = cleanup_git_state.run_command(
        (
            "git",
            "for-each-ref",
            "--format=%(refname:short)\t%(committerdate:unix)",
            "refs/heads/vps-loop/item-*-superseded-*",
        ),
        cwd=repo,
    ).stdout
    branches: dict[str, int] = {}
    for line in output.splitlines():
        if not line.strip():
            continue
        name, epoch = line.split("\t", 1)
        if SUPERSEDED_BRANCH_RE.match(name):
            branches[name] = int(epoch)
    return dict(sorted(branches.items()))


def decide_backup_branch(
    branch: str, commit_epoch: int, *, now_epoch: int, retention_days: int
) -> BackupBranchDecision:
    """Decide whether a superseded-backup branch has aged past its retention window."""

    age_days = (now_epoch - commit_epoch) / 86400
    if age_days > retention_days:
        return BackupBranchDecision(
            branch, "delete", f"backing commit is {age_days:.1f}d old, past {retention_days}d retention", commit_epoch
        )
    return BackupBranchDecision(
        branch, "keep", f"backing commit is {age_days:.1f}d old, within {retention_days}d retention", commit_epoch
    )


def delete_local_branch(repo: Path, branch: str) -> None:
    """Delete one local branch."""

    cleanup_git_state.run_git(repo, "branch", "-D", "--", branch)


def run_backup_branch_pruning(repo: Path, *, retention_days: int, apply: bool, log_file: Path) -> None:
    """Plan (and optionally apply) removal of superseded-backup branches past retention."""

    print(f"== Stale superseded-backup branch pruning (retention={retention_days}d) ==")
    now_epoch = int(time.time())
    decisions = [
        decide_backup_branch(branch, commit_epoch, now_epoch=now_epoch, retention_days=retention_days)
        for branch, commit_epoch in load_local_superseded_branches(repo).items()
    ]
    for decision in decisions:
        print(f"{decision.action.upper():6} {decision.branch} — {decision.reason}")
    delete_count = sum(decision.action == "delete" for decision in decisions)
    print(f"Summary: {delete_count} deletable, {len(decisions) - delete_count} retained")
    if delete_count and not apply:
        print("Dry run only. Re-run with --apply to remove the listed backup branches.")
    if not apply:
        return

    for decision in decisions:
        if decision.action != "delete":
            continue
        # Rechecking the branch still exists immediately beforehand -- a
        # concurrent interactive session could have already removed it.
        exists = cleanup_git_state.run_git(repo, "rev-parse", "--verify", f"refs/heads/{decision.branch}", check=False)
        if exists.returncode != 0:
            continue
        try:
            delete_local_branch(repo, decision.branch)
        except (CleanupError, OSError) as exc:
            message = f"backup pruning: ERROR deleting {decision.branch}: {exc}"
            print(message, file=sys.stderr)
            log_line(log_file, message)
            continue
        print(f"DELETED {decision.branch}")
        log_line(log_file, f"backup pruning: DELETED {decision.branch} — {decision.reason}")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main(argv: Sequence[str] | None = None) -> int:
    """CLI entry point."""

    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--repo", type=Path, default=Path("/root/transit-app"), help="Any worktree in the target repo")
    parser.add_argument("--base", default="main", help="Up-to-date integration branch (default: main)")
    parser.add_argument("--remote", default="origin", help="Remote used for both base validation and branch cleanup")
    parser.add_argument("--protect", action="append", default=[], metavar="BRANCH", help="Extra local branch to retain")
    parser.add_argument("--apply", action="store_true", help="Apply the printed plans; default is a dry run")
    parser.add_argument("--lock-file", type=Path, default=DEFAULT_LOCK_FILE, help="Lock shared with claude-loop.sh")
    parser.add_argument("--log-file", type=Path, default=DEFAULT_LOG_FILE, help="Deletion log (not refactor-log.md)")
    parser.add_argument(
        "--retention-days", type=int, default=DEFAULT_RETENTION_DAYS, help="Backup-branch retention window in days"
    )
    args = parser.parse_args(argv)

    log_file: Path = args.log_file
    lock_handle = try_acquire_lock(args.lock_file)
    if lock_handle is None:
        message = f"SKIP: {args.lock_file} is held (a /vps-loop-run tick is likely mid-flight); not touching git state"
        print(message)
        log_line(log_file, message)
        return 0

    try:
        try:
            repo_root = cleanup_git_state.run_git(args.repo.resolve(), "rev-parse", "--show-toplevel").stdout.strip()
            repo = Path(repo_root).resolve()
        except (CleanupError, OSError) as exc:
            print(f"ERROR: {exc}", file=sys.stderr)
            log_line(log_file, f"ERROR: could not resolve --repo {args.repo}: {exc}")
            return 2

        protected = {args.base, "production", *args.protect}
        exit_code = 0

        stages: tuple[tuple[str, Callable[[], None]], ...] = (
            (
                "local cleanup",
                lambda: run_local_cleanup(
                    repo, base=args.base, remote=args.remote, protected=protected, apply=args.apply, log_file=log_file
                ),
            ),
            (
                "remote branch cleanup",
                lambda: run_remote_branch_cleanup(repo, remote=args.remote, apply=args.apply, log_file=log_file),
            ),
            (
                "backup branch pruning",
                lambda: run_backup_branch_pruning(
                    repo, retention_days=args.retention_days, apply=args.apply, log_file=log_file
                ),
            ),
        )
        for stage, runner in stages:
            try:
                runner()
            except (CleanupError, HygieneError, OSError) as exc:
                print(f"ERROR ({stage}): {exc}", file=sys.stderr)
                log_line(log_file, f"ERROR: {stage} failed: {exc}")
                exit_code = 2

        return exit_code
    finally:
        release_lock(lock_handle)


if __name__ == "__main__":
    raise SystemExit(main())
