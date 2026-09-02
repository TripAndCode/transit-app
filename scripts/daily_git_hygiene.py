#!/usr/bin/env python3
"""Plan or apply daily git hygiene: local, remote, backup-branch, and venv cleanup.

This closes four gaps `/vps-loop-run` only handles reactively (or not at all):

1. Local branch/worktree cleanup (`scripts/cleanup_git_state.py`) currently
   only runs as a side effect of a loop tick, so it never runs during a long
   idle stretch or while the loop is stuck.
2. Merged `vps-loop/item-<N>` branches on GitHub are never deleted by the
   loop itself (accepted operational debt, batched by a human "when it's
   worth the time").
3. `vps-loop/item-<N>-superseded-<sha>` backup branches (created by the
   loop's Step 2b before a judgment-based delete) have no automated prune
   path at all.
4. Poetry names a project's virtualenv by hashing its absolute path, so a
   worktree this script's own stage 1 (or the loop's own Step 2/2b) deletes
   leaves its now-orphaned venv behind at ~5-6GB apiece with no automated
   prune path -- poetry itself never revisits a path once it stops existing.
   A handful of these is enough to fill a small VPS's root disk.

This script must never race a live `/vps-loop-run` tick: it acquires the
same `/tmp/claude-loop.lock` lock file non-blockingly before touching
anything and skips cleanly (not an error) if the loop is mid-tick. A single
fixed-time daily cron trigger has no retry if it loses that race, so the
crontab should invoke this hourly; a same-day completion marker
(`--state-file`, default `/root/.daily_git_hygiene_last_success`) keeps the
actual cleanup itself running at most once per calendar day regardless of
how often the trigger fires.

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
import shutil
import subprocess
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
DEFAULT_STATE_FILE = Path("/root/.daily_git_hygiene_last_success")
DEFAULT_RETENTION_DAYS = 30
DEFAULT_POETRY_VENV_ROOT = Path("/root/.cache/pypoetry/virtualenvs")
DEFAULT_MIN_VENV_AGE_HOURS = 24
DEFAULT_MAX_VENV_DELETES_PER_RUN = 20
POETRY_ENV_INFO_TIMEOUT_SECONDS = 10
# Poetry's own venv-naming scheme for this project ("<name>-<hash>-py<major.minor>");
# matches only this repo's own venvs, never an unrelated project sharing the same
# shared virtualenvs.path (e.g. a poetry-managed CLI tool used across other work).
POETRY_VENV_GLOB = "transit-delay-app-*"

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


@dataclass(frozen=True)
class VenvDecision:
    """A keep/delete decision for one poetry virtualenv directory."""

    path: Path
    action: Literal["keep", "delete"]
    reason: str


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
# Same-day completion marker
# ---------------------------------------------------------------------------


def today_utc() -> str:
    """Today's UTC date as `YYYY-MM-DD`, this job's unit of "already ran"."""

    return time.strftime("%Y-%m-%d", time.gmtime())


def already_succeeded_today(state_file: Path, *, today: str | None = None) -> bool:
    """True if `state_file` already records `today` (default: `today_utc()`) as completed.

    A single fixed cron time (e.g. once daily) can keep losing the
    `try_acquire_lock` race to an unrelated `/vps-loop-run` tick with no
    retry within that invocation (see that function's own docstring).
    Pairing a same-day completion marker with a much more frequent cron
    trigger (this job stays idempotent per day either way) turns that into
    an hourly retry instead of a 24-hour one, without ever running the
    real cleanup twice in one day.

    Takes `today` explicitly (rather than each caller in `main` calling
    `today_utc()` separately) so a run whose wall-clock execution straddles
    a UTC midnight still compares and records the same calendar day it
    started on, instead of the pre-check and the eventual marker disagreeing.

    Any `OSError` other than a missing file (permission denied, the path
    being a directory, ...) is treated the same as "not yet succeeded" --
    every stage this gates is already idempotent, so failing open here
    costs at most one redundant hourly attempt, not a false skip of real
    cleanup.
    """

    try:
        return state_file.read_text(encoding="utf-8").strip() == (today if today is not None else today_utc())
    except OSError:
        return False


def mark_succeeded_today(state_file: Path, log_file: Path, *, today: str | None = None) -> None:
    """Record `today` (default: `today_utc()`) as this job's last fully-clean run.

    Failure to write is logged, not raised -- this runs only after the real
    cleanup already completed, so losing the marker costs one redundant
    hourly retry tomorrow (extra `gh`/`git` calls, not a correctness issue),
    never a false "done" or a crash before the lock in `main`'s `finally`
    block gets to release.
    """

    try:
        state_file.parent.mkdir(parents=True, exist_ok=True)
        state_file.write_text(f"{today if today is not None else today_utc()}\n", encoding="utf-8")
    except OSError as exc:
        message = f"WARNING: could not write completion marker {state_file}: {exc}"
        print(message, file=sys.stderr)
        log_line(log_file, message)


# ---------------------------------------------------------------------------
# 1. Local branch/worktree cleanup -- delegates to cleanup_git_state.py
# ---------------------------------------------------------------------------


def run_local_cleanup(repo: Path, *, base: str, remote: str, protected: set[str], apply: bool, log_file: Path) -> bool:
    """Plan (and optionally apply) `cleanup_git_state`'s local cleanup, logging deletes.

    Fetches `remote` first: `build_plan`'s `validate_base` requires local
    `refs/heads/{base}` to exactly match `refs/remotes/{remote}/{base}`, and
    this job is specifically meant to run during idle stretches where nothing
    else has refreshed that remote-tracking ref recently.

    Unlike the other two stages, `cleanup_git_state.apply_plan` has no
    per-item swallow-and-continue -- any problem raises `CleanupError`
    immediately, which `main`'s own stage loop already catches. Always
    returns True (or doesn't return at all) for that reason; the bool
    return exists only so all three stages share one uniform contract.
    """

    print("== Local branch/worktree cleanup (cleanup_git_state) ==")
    cleanup_git_state.run_git(repo, "fetch", "--prune", remote)
    decisions = cleanup_git_state.build_plan(repo, base=base, remote=remote, protected=protected)
    cleanup_git_state.print_plan(decisions, applying=apply)
    if not apply:
        return True

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

    return True


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


def run_remote_branch_cleanup(repo: Path, *, remote: str, apply: bool, log_file: Path) -> bool:
    """Plan (and optionally apply) deletion of merged, non-stacked `vps-loop/item-*` branches.

    Returns False if any individual branch's tip-check or delete failed --
    those are deliberately swallowed and logged per-branch (a transient
    failure on one branch must not abort the rest), but the caller still
    needs to know the stage wasn't fully clean, since that decides whether
    today can be marked done (see `main`'s `mark_succeeded_today` gate).
    """

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
        return True

    all_clean = True

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
            all_clean = False
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
            all_clean = False
            continue
        print(f"DELETED {remote}/{decision.branch}")
        log_line(log_file, f"remote cleanup: DELETED {remote}/{decision.branch} — {decision.reason}")

    return all_clean


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


def run_backup_branch_pruning(repo: Path, *, retention_days: int, apply: bool, log_file: Path) -> bool:
    """Plan (and optionally apply) removal of superseded-backup branches past retention.

    Returns False if any individual branch's delete failed -- deliberately
    swallowed and logged per-branch (see `run_remote_branch_cleanup`'s
    docstring for why), but still surfaced so `main` doesn't mark today
    done on a stage that wasn't actually fully clean.
    """

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
        return True

    all_clean = True
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
            all_clean = False
            continue
        print(f"DELETED {decision.branch}")
        log_line(log_file, f"backup pruning: DELETED {decision.branch} — {decision.reason}")

    return all_clean


# ---------------------------------------------------------------------------
# 4. Orphaned poetry venv pruning
# ---------------------------------------------------------------------------


def poetry_env_path(location: Path) -> Path | None:
    """Return the poetry virtualenv path active at `location`, or None if
    poetry can't resolve one there.

    `None` covers two outcomes `poetry env info --path` cannot distinguish
    from each other by exit code or output alone (confirmed live: both
    produce exit 1 with empty stdout AND empty stderr) -- "no venv created
    for this location yet" (benign) and "poetry itself failed for an
    unrelated, possibly transient reason" (not benign, if a real venv
    exists there and is in use). Callers that need to tell these apart
    must do so themselves; this function only ever reports "resolved" or
    "not resolved."

    A hard timeout guards against `poetry env info` hanging (config-file
    lock contention, a keyring/dbus stall in a headless environment) --
    this runs under `/tmp/claude-loop.lock`, and a hang here must not hold
    that lock indefinitely and block a live `/vps-loop-run` tick.
    """

    try:
        result = subprocess.run(
            ("poetry", "env", "info", "--path"),
            cwd=location,
            capture_output=True,
            text=True,
            timeout=POETRY_ENV_INFO_TIMEOUT_SECONDS,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if result.returncode != 0:
        return None
    path = result.stdout.strip()
    return Path(path).resolve() if path else None


def compute_in_use_poetry_venvs(repo: Path, *, min_age_hours: float) -> set[Path]:
    """Every currently in-use poetry venv path: the main repo plus every worktree.

    The main checkout's own venv must always resolve -- it's never "new,"
    so any failure there raises `HygieneError` unconditionally, refusing to
    prune anything this run: `poetry_env_path` cannot tell "no venv yet"
    apart from "poetry failed for an unrelated reason" (see its own
    docstring), so a real, in-use main-checkout venv could be the one that
    failed to resolve, and there is no safe way to guess which.

    A worktree gets two, narrower grace conditions before the same
    fail-closed treatment applies:
    - `git worktree list`'s own `prunable` flag, or the path simply not
      existing on disk, means the administrative entry outlived the actual
      directory (removed out-of-band, or pending its own `git worktree
      prune`) -- unambiguously "nothing runs out of here," not one of the
      two cases above, so it's skipped rather than raised on.
    - A worktree younger than `min_age_hours` (mirroring the same grace
      period `run_orphaned_venv_pruning` already applies to venv ages) may
      simply not have run any poetry/backend command yet -- e.g. a
      freshly-dispatched, frontend-only worker. Treating every such
      worktree as a fail-closed abort would leave this stage (and this
      job's once-daily completion marker, since a failing stage prevents
      `mark_succeeded_today`) permanently unable to complete for as long as
      any such worktree exists, which is an ordinary, common state, not a
      rare edge case.
    Past that grace period, an unresolvable worktree is treated exactly
    like the main checkout: the whole computation raises, since a real,
    in-use venv could be the one poetry transiently failed to resolve. The
    cost of raising is a delayed prune (retried next hourly trigger), never
    a false "not in use."
    """

    main_venv = poetry_env_path(repo)
    if main_venv is None:
        raise HygieneError(f"could not resolve the main checkout's own poetry venv at {repo}; refusing to prune")
    resolved_repo = repo.resolve()
    worktrees = cleanup_git_state.parse_worktrees(
        cleanup_git_state.run_git(repo, "worktree", "list", "--porcelain").stdout
    )
    now_epoch = time.time()
    in_use = {main_venv}
    for worktree in worktrees:
        resolved_worktree = worktree.path.resolve()
        if resolved_worktree == resolved_repo:
            continue  # already queried above; avoid a redundant poetry spawn
        if worktree.prunable or not worktree.path.exists():
            continue
        venv = poetry_env_path(worktree.path)
        if venv is not None:
            in_use.add(venv)
            continue
        try:
            age_hours = (now_epoch - worktree.path.stat().st_mtime) / 3600
        except OSError:
            continue  # vanished between the exists() check and here -- same as the already-gone case above
        if age_hours < min_age_hours:
            continue
        raise HygieneError(
            f"could not resolve poetry venv for worktree {worktree.path} "
            f"({age_hours:.1f}h old, past the {min_age_hours}h grace period); "
            "refusing to prune any orphaned venv this run"
        )
    return in_use


def run_orphaned_venv_pruning(
    repo: Path,
    *,
    venv_root: Path,
    min_age_hours: float,
    max_deletes_per_run: int,
    apply: bool,
    log_file: Path,
) -> bool:
    """Plan (and optionally apply) removal of this project's poetry venvs whose
    worktree no longer exists.

    `min_age_hours` is a second, independent safety margin on top of the
    in-use correlation above: a venv younger than this is never deleted even
    if it doesn't match any currently-known worktree, in case a worktree was
    created (and its venv installed) after this tick's own `git worktree
    list` snapshot but before this stage reached the apply loop.

    `max_deletes_per_run` is a circuit breaker, not a normal-operation limit:
    steady-state hourly pruning should only ever find zero or one orphan.
    A plan exceeding this is treated as a signal something is systemically
    wrong (e.g. a burst of merges, or the in-use correlation itself
    misbehaving) and refuses to delete anything until a human looks,
    rather than silently deleting a large, irreversible batch.
    """

    print(f"== Orphaned poetry venv pruning ({venv_root}) ==")
    if not venv_root.is_dir():
        print(f"{venv_root} does not exist, nothing to do")
        return True

    in_use = compute_in_use_poetry_venvs(repo, min_age_hours=min_age_hours)
    now_epoch = time.time()
    candidates = sorted(path for path in venv_root.glob(POETRY_VENV_GLOB) if path.is_dir())
    decisions: list[VenvDecision] = []
    for candidate in candidates:
        resolved = candidate.resolve()
        age_hours = (now_epoch - candidate.stat().st_mtime) / 3600
        if resolved in in_use:
            decisions.append(VenvDecision(candidate, "keep", "in use by the main checkout or a current worktree"))
        elif age_hours < min_age_hours:
            decisions.append(
                VenvDecision(candidate, "keep", f"only {age_hours:.1f}h old, within {min_age_hours}h safety margin")
            )
        else:
            decisions.append(VenvDecision(candidate, "delete", f"no matching worktree, {age_hours:.1f}h old"))
    for decision in decisions:
        print(f"{decision.action.upper():6} {decision.path} — {decision.reason}")
    delete_count = sum(decision.action == "delete" for decision in decisions)
    print(f"Summary: {delete_count} deletable, {len(decisions) - delete_count} retained")
    if delete_count == 0:
        return True
    if not apply:
        print("Dry run only. Re-run with --apply to remove the listed orphaned venvs.")
        return True
    if delete_count > max_deletes_per_run:
        message = (
            f"venv pruning: REFUSING to delete {delete_count} venvs in one run "
            f"(exceeds --max-venv-deletes-per-run={max_deletes_per_run}); needs a human to review the plan above"
        )
        print(message, file=sys.stderr)
        log_line(log_file, message)
        return False

    all_clean = True
    # Re-fetch the in-use set once right before applying (not once per venv) --
    # mirrors run_remote_branch_cleanup's fresh_dependents re-check -- to catch a
    # worktree that started using one of these paths during planning. Only worth
    # the extra `poetry` spawns per worktree when there's actually something to
    # delete (delete_count == 0 already returned above).
    fresh_in_use = compute_in_use_poetry_venvs(repo, min_age_hours=min_age_hours)
    for decision in decisions:
        if decision.action != "delete":
            continue
        if decision.path.resolve() in fresh_in_use:
            message = f"venv pruning: SKIPPED {decision.path} — now in use, appeared since planning"
            print(message)
            log_line(log_file, message)
            continue
        try:
            shutil.rmtree(decision.path)
        except OSError as exc:
            message = f"venv pruning: ERROR deleting {decision.path}: {exc}"
            print(message, file=sys.stderr)
            log_line(log_file, message)
            all_clean = False
            continue
        print(f"DELETED {decision.path}")
        log_line(log_file, f"venv pruning: DELETED {decision.path}")

    return all_clean


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
        "--state-file", type=Path, default=DEFAULT_STATE_FILE, help="Marks the last calendar day this job completed"
    )
    parser.add_argument(
        "--retention-days", type=int, default=DEFAULT_RETENTION_DAYS, help="Backup-branch retention window in days"
    )
    parser.add_argument(
        "--venv-root", type=Path, default=DEFAULT_POETRY_VENV_ROOT, help="Poetry's virtualenvs.path to prune within"
    )
    parser.add_argument(
        "--min-venv-age-hours",
        type=float,
        default=DEFAULT_MIN_VENV_AGE_HOURS,
        help="Never prune a venv younger than this, even if no worktree currently matches it",
    )
    parser.add_argument(
        "--max-venv-deletes-per-run",
        type=int,
        default=DEFAULT_MAX_VENV_DELETES_PER_RUN,
        help="Refuse (rather than delete) an orphaned-venv plan larger than this in one run",
    )
    args = parser.parse_args(argv)

    log_file: Path = args.log_file

    # Captured once so a run whose execution straddles a UTC midnight still
    # checks and (if it succeeds) records the same calendar day throughout,
    # rather than the pre-check and the eventual marker each calling
    # `today_utc()` fresh and disagreeing.
    run_day = today_utc()

    # Checked before the lock, and not logged: an hourly cron trigger hitting
    # this on a day it already completed is the expected common case, not
    # something worth a log line every time.
    if already_succeeded_today(args.state_file, today=run_day):
        print(f"Already completed today ({run_day}) per {args.state_file}; nothing to do")
        return 0

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

        stages: tuple[tuple[str, Callable[[], bool]], ...] = (
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
            (
                "orphaned venv pruning",
                lambda: run_orphaned_venv_pruning(
                    repo,
                    venv_root=args.venv_root,
                    min_age_hours=args.min_venv_age_hours,
                    max_deletes_per_run=args.max_venv_deletes_per_run,
                    apply=args.apply,
                    log_file=log_file,
                ),
            ),
        )
        for stage, runner in stages:
            try:
                stage_clean = runner()
            except (CleanupError, HygieneError, OSError) as exc:
                print(f"ERROR ({stage}): {exc}", file=sys.stderr)
                log_line(log_file, f"ERROR: {stage} failed: {exc}")
                exit_code = 2
                continue
            if not stage_clean:
                # A per-item failure inside the stage (already logged there,
                # e.g. one branch's delete or tip-check erroring) -- the
                # stage itself didn't raise, but it wasn't fully clean either.
                exit_code = 2

        # Only a fully-clean --apply run marks today done. A dry run (the
        # default, and the documented way to preview what a run would do)
        # reaches exit_code == 0 just as easily as a real cleanup does, since
        # every stage returns early before deleting anything -- marking it
        # done would silently cancel the day's real --apply cron run the
        # next time it fires. A stage error (raised or per-item) must also
        # not mark today done, so it retries on the next hourly trigger
        # instead of waiting a full day.
        if args.apply and exit_code == 0:
            mark_succeeded_today(args.state_file, log_file, today=run_day)

        return exit_code
    finally:
        release_lock(lock_handle)


if __name__ == "__main__":
    raise SystemExit(main())
