#!/usr/bin/env python3
"""Plan or apply daily git hygiene: local branch/worktree and orphaned-venv cleanup.

Two stages, each closing a gap nothing else sweeps on a schedule:

1. Local branch/worktree cleanup (`scripts/cleanup_git_state.py`), which
   otherwise only runs when someone remembers `/cleanup-merged`.
2. Poetry names a project's virtualenv by hashing its absolute path, so a
   worktree stage 1 (or a person) deletes leaves its now-orphaned venv behind
   at several GB apiece -- poetry itself never revisits a path once it stops
   existing. A handful of these is enough to fill a small VPS's root disk.

A single lock file (`--lock-file`, taken non-blockingly) keeps two runs from
overlapping; a run that finds it held skips cleanly rather than waiting. A
single fixed-time daily cron trigger has no retry if it fails, so the crontab
should invoke this hourly; a same-day completion marker (`--state-file`,
default `/root/.daily_git_hygiene_last_success`) keeps the actual cleanup
running at most once per calendar day regardless of how often it fires.

Every deletion is appended to a dedicated hygiene log (default
`/root/git-hygiene.log`).

Like `cleanup_git_state.py`, planning is the default; pass `--apply` to
actually delete anything.
"""

from __future__ import annotations

import argparse
import contextlib
import fcntl
import importlib.util
import io
import shutil
import subprocess
import sys
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import IO, TYPE_CHECKING, Literal, Sequence

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

# `importlib` hands a type checker a bare `ModuleType`, so an attribute read
# off `cleanup_git_state` is an untyped value -- usable at runtime, but not
# valid in an annotation. The static import below names the same objects from
# the same file so the type checker sees their real types; the
# runtime branch keeps using the dynamically loaded module object, which is
# the only one that exists when `scripts/` isn't importable as a package.
if TYPE_CHECKING:
    from scripts.cleanup_git_state import (
        REVIEW_WORKTREE_PARENT_DIR,
        REVIEW_WORKTREE_PREFIX,
        CleanupError,
        PullRequest,
        is_review_worktree,
    )
else:
    PullRequest = cleanup_git_state.PullRequest
    CleanupError = cleanup_git_state.CleanupError
    # The review-worktree convention lives in cleanup_git_state, the deletion
    # authority, so the branch/worktree stage and the venv stage read one rule.
    REVIEW_WORKTREE_PARENT_DIR = cleanup_git_state.REVIEW_WORKTREE_PARENT_DIR
    REVIEW_WORKTREE_PREFIX = cleanup_git_state.REVIEW_WORKTREE_PREFIX
    is_review_worktree = cleanup_git_state.is_review_worktree

DEFAULT_LOCK_FILE = Path("/tmp/transit-git-hygiene.lock")
DEFAULT_LOG_FILE = Path("/root/git-hygiene.log")
DEFAULT_STATE_FILE = Path("/root/.daily_git_hygiene_last_success")
DEFAULT_POETRY_VENV_ROOT = Path("/root/.cache/pypoetry/virtualenvs")
DEFAULT_MIN_VENV_AGE_HOURS = 24
DEFAULT_MAX_VENV_DELETES_PER_RUN = 20
POETRY_ENV_INFO_TIMEOUT_SECONDS = 10
# Poetry's own venv-naming scheme for this project ("<name>-<hash>-py<major.minor>");
# matches only this repo's own venvs, never an unrelated project sharing the same
# shared virtualenvs.path (e.g. a poetry-managed CLI tool used across other work).
POETRY_VENV_GLOB = "transit-delay-app-*"


class HygieneError(RuntimeError):
    """Raised when the hygiene job cannot make a conservative decision."""


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

    Non-blocking (`LOCK_EX | LOCK_NB`): a second run must skip cleanly rather
    than wait for the first to finish.
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

    A single fixed cron time (e.g. once daily) has no retry if that run
    fails or finds the lock held. Pairing a same-day completion marker with a much more frequent cron
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

    Unlike the venv stage, `cleanup_git_state.apply_plan` has no per-item
    swallow-and-continue -- any problem raises `CleanupError` immediately,
    which `main`'s own stage loop already catches. Always returns True (or
    doesn't return at all) for that reason; the bool return exists only so
    both stages share one uniform contract.
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
# 2. Orphaned poetry venv pruning
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
    this runs under the hygiene lock, and a hang here must not hold that lock
    indefinitely and block every later run.
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


def compute_in_use_poetry_venvs(repo: Path, main_venv: Path, *, min_age_hours: float) -> set[Path]:
    """Every currently in-use poetry venv path: `main_venv` plus every worktree's own.

    `main_venv` is resolved and validated exactly once by the caller
    (`run_orphaned_venv_pruning`), not re-derived here: a second, separate
    `poetry env info --path` spawn for the same `repo` could transiently
    fail even when the first one just succeeded, which would otherwise
    raise a confusing "`--venv-root` is misconfigured" error for what is
    really an unrelated, one-off `poetry` hiccup.

    A worktree gets three, narrower grace conditions before the same
    fail-closed treatment applies:
    - `is_review_worktree` matches `/review-pr`'s own worktree convention.
      Neither documented workflow that creates this shape (`/review-pr`,
      `/follow-up-pr-review`) ever runs a poetry command with it as cwd, so
      an unresolved venv here is overwhelmingly "never created," not
      "transiently unresolved" -- unlike the agent-worktree case below,
      whose unresolved venv could be a real, load-bearing one.
      `poetry_env_path` is still attempted first, exactly like any other
      worktree -- a resolved result is added to `in_use` the same as
      anywhere else, so a venv created out-of-band despite the documented
      read-only workflow is never dropped. Only a `None` result gets the
      exemption, and only when the worktree still exists: no age check, no
      raise.
    - `git worktree list`'s own `prunable` flag means the administrative
      entry outlived the actual directory (removed out-of-band, or pending
      its own `git worktree prune`) -- unambiguously "nothing runs out of
      here," not the venv-never-created-versus-transiently-unresolved
      ambiguity `poetry_env_path`'s `None` leaves open, so it's skipped
      rather than raised on. A `locked` worktree whose directory is merely
      absent is NOT treated the same way: git itself refuses to mark a locked
      worktree `prunable` even when its directory is gone (confirmed
      live), since locking is meant to protect it from exactly this kind
      of cleanup -- so an absent-but-locked worktree still falls through
      to the fail-closed path below, rather than being silently skipped.
    - A worktree younger than `min_age_hours` (mirroring the same grace
      period `run_orphaned_venv_pruning` already applies to venv ages) may
      simply not have run any poetry/backend command yet -- e.g. a
      freshly-dispatched, frontend-only worker. Treating every such
      worktree as a fail-closed abort would leave this stage (and this
      job's once-daily completion marker, since a failing stage prevents
      `mark_succeeded_today`) permanently unable to complete for as long as
      any such worktree exists, which is an ordinary, common state, not a
      rare edge case. Age is read from the linked worktree's `.git` FILE
      (not the worktree's own top-level directory): `git worktree add`
      writes that file at creation and (confirmed live) leaves it alone
      across ordinary operations (status/fetch/commit/switch/rebase/`gc`/
      lock/unlock), unlike a directory's own mtime, which resets on any
      root-level create/delete inside it (a `.mypy_cache/`, an untracked
      scratch file, a branch switch that adds/removes a root file) --
      all routine activity for an actively-used worktree, which would
      otherwise make a genuinely old, in-use worktree look "young" and
      skip the very check meant to protect it. `git worktree move` and a
      pointer-rewriting `git worktree repair` DO reset this stamp -- the
      former is self-protecting (the path itself changes, so poetry's own
      hash changes too, and the old path's venv is now a genuine orphan),
      but a `repair` after the *main* checkout moved, while every worktree
      path stays put, resets every worktree's stamp without their venvs
      actually changing -- a known, narrow residual, not something this
      code detects.
    Past that grace period, an unresolvable worktree is treated exactly
    like the main checkout: the whole computation raises, since a real,
    in-use venv could be the one poetry transiently failed to resolve. The
    cost of raising is a delayed prune (retried next hourly trigger), never
    a false "not in use." A linked worktree's `.git` is always a file
    (never a directory -- only the main checkout's own `.git` is one), so
    an unexpected shape there also raises rather than silently guessing.

    A known, undetected residual on the far side of that same trade-off: an
    agent worktree whose sandbox has no `poetry install` permission can go
    past `min_age_hours` never having created a venv either, for a
    structurally different reason than the review-worktree case above -- but
    nothing here can tell that apart from a worktree whose poetry install is
    merely running late or a transient hiccup hid a real one, since
    `poetry_env_path` returns the same `None` for all three.
    Unlike the review-worktree case, this is not exempted: doing so by
    matching `.claude/worktrees/agent-*` would also exempt the much more
    common worktree that *did* successfully create a real venv on a run
    where `poetry env info` merely hiccupped, which is exactly the false
    "not in use" this function exists to prevent. Left to raise (and delay
    pruning) until a human confirms which case it actually is.

    Assumes `repo` is the only clone of this project on the host, and that
    no worktree path is ever reused after removal within one venv's
    `min_age_hours` window: an unrelated second clone, or a fresh worktree
    recreated at a path poetry previously hashed for a since-removed one,
    would both be indistinguishable from this function's own inputs alone.
    Neither is something this code detects or corrects for -- both are
    deployment/usage assumptions, not gaps checked here.
    """

    resolved_repo = repo.resolve()
    worktrees = cleanup_git_state.parse_worktrees(
        cleanup_git_state.run_git(repo, "worktree", "list", "--porcelain").stdout
    )
    now_epoch = time.time()
    in_use = {main_venv}
    for worktree in worktrees:
        resolved_worktree = worktree.path.resolve()
        if resolved_worktree == resolved_repo:
            continue  # main_venv (the caller's, not re-derived here) already covers this one
        if worktree.prunable or (not worktree.locked and not worktree.path.exists()):
            continue
        venv = poetry_env_path(worktree.path)
        if venv is not None:
            in_use.add(venv)
            continue
        # `worktree.path.exists()`: a review worktree is left in place indefinitely by
        # design, so it is present by definition -- requiring existence here preserves
        # the locked-and-absent fail-closed raise below for this shape too, matching the
        # invariant the `prunable`/absent-and-unlocked check above already states.
        if worktree.path.exists() and is_review_worktree(resolved_worktree):
            continue
        creation_stamp = worktree.path / ".git"
        if not creation_stamp.is_file():
            # Not the linked-worktree shape at all (or already gone) -- no
            # creation-stable timestamp to trust, so no grace period.
            raise HygieneError(
                f"worktree {worktree.path} has no resolvable venv and no `.git` file to age-check "
                "(expected a linked worktree); refusing to prune any orphaned venv this run"
            )
        # Deliberately `except FileNotFoundError`, not the broader `OSError`: a
        # *persistent* stat failure (permission denied, a stale network mount,
        # an I/O error -- e.g. exactly the portable-device/network-share case
        # `git worktree lock` exists for) is not "vanished" and must not be
        # treated the same as it. Left uncaught here, it propagates out of this
        # function entirely and is still fail-closed -- `main()`'s own stage
        # loop already catches `OSError` generically -- just with a less
        # specific message than the HygieneError raises elsewhere in this
        # function. Swallowing it here instead would be a false "not in use",
        # breaking this function's own stated guarantee.
        try:
            age_hours = (now_epoch - creation_stamp.stat().st_mtime) / 3600
        except FileNotFoundError:
            continue  # vanished between the is_file() check and here -- same as the already-gone case above
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
    rather than silently deleting a large, irreversible batch. Checked (and
    printed) in dry-run mode too, so a preview never shows a plan that the
    real `--apply` run would then simply refuse.
    """

    print(f"== Orphaned poetry venv pruning ({venv_root}) ==")

    # Resolved exactly once here (not inside compute_in_use_poetry_venvs, and not
    # called a second time below) so a transient poetry hiccup on the later
    # re-check can never masquerade as "--venv-root is misconfigured".
    main_venv = poetry_env_path(repo)
    if main_venv is None:
        raise HygieneError(
            f"could not resolve this checkout's own poetry venv at {repo} (is `poetry` on PATH and "
            "working?); refusing to prune -- this is a poetry/environment problem, not necessarily "
            "a --venv-root misconfiguration"
        )

    # `venv_root.glob(...)` on a nonexistent directory simply yields nothing (no
    # error), so this one enumeration doubles as the "does --venv-root even
    # exist" check -- there's no separate is_dir() special case to keep in sync.
    now_epoch = time.time()
    candidates = sorted(path for path in venv_root.glob(POETRY_VENV_GLOB) if path.is_dir() and not path.is_symlink())

    # Self-validating positive control: if `--venv-root` is misconfigured (wrong
    # platform default, including simply not existing) or `POETRY_VENV_GLOB` no
    # longer matches (a renamed project), this stage would otherwise "succeed" by
    # finding zero candidates forever, silently enforcing nothing -- the one
    # thing it exists to prevent a repeat of. Checking against the SAME
    # `candidates` list the rest of this function actually acts on (rather than
    # a separate parent-path/glob string comparison) means this control can
    # never pass while the real enumeration it's meant to certify comes back
    # empty or wrong.
    if not any(candidate.resolve() == main_venv for candidate in candidates):
        raise HygieneError(
            f"--venv-root {venv_root} does not enumerate this checkout's own poetry venv "
            f"({main_venv}) via {POETRY_VENV_GLOB!r}; refusing to prune what may be the wrong directory entirely"
        )

    in_use = compute_in_use_poetry_venvs(repo, main_venv, min_age_hours=min_age_hours)
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
    if delete_count > max_deletes_per_run:
        message = (
            f"venv pruning: {'would refuse' if not apply else 'REFUSING'} to delete {delete_count} venvs in one run "
            f"(exceeds --max-venv-deletes-per-run={max_deletes_per_run}); needs a human to review the plan above"
        )
        print(message, file=sys.stderr)
        if apply:
            log_line(log_file, message)
            return False
        return True
    if not apply:
        print("Dry run only. Re-run with --apply to remove the listed orphaned venvs.")
        return True

    all_clean = True
    # Re-fetch the in-use set once right before applying (not once per venv) --
    # mirrors run_remote_branch_cleanup's fresh_dependents re-check -- to catch a
    # worktree that started using one of these paths during planning. Only worth
    # the extra `poetry` spawns per worktree when there's actually something to
    # delete (delete_count == 0 already returned above).
    fresh_in_use = compute_in_use_poetry_venvs(repo, main_venv, min_age_hours=min_age_hours)
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
    parser.add_argument("--remote", default="origin", help="Remote used for base validation")
    parser.add_argument("--protect", action="append", default=[], metavar="BRANCH", help="Extra local branch to retain")
    parser.add_argument("--apply", action="store_true", help="Apply the printed plans; default is a dry run")
    parser.add_argument("--lock-file", type=Path, default=DEFAULT_LOCK_FILE, help="Keeps two runs from overlapping")
    parser.add_argument("--log-file", type=Path, default=DEFAULT_LOG_FILE, help="Deletion log")
    parser.add_argument(
        "--state-file", type=Path, default=DEFAULT_STATE_FILE, help="Marks the last calendar day this job completed"
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
        message = f"SKIP: {args.lock_file} is held (another run is mid-flight); not touching git state"
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
