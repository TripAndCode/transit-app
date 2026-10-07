#!/usr/bin/env python3
"""Plan or apply git hygiene: local branch/worktree and orphaned-venv cleanup.

Two stages, each closing a gap nothing else sweeps:

1. Local branch/worktree cleanup (`scripts/cleanup_git_state.py`), which
   otherwise only runs when someone remembers `/cleanup-merged`.
2. Poetry names a project's virtualenv by hashing its absolute path, so a
   worktree stage 1 (or a person) deletes leaves its now-orphaned venv behind
   -- poetry itself never revisits a path once it stops existing. A handful
   of full environments is enough to fill a small disk.

`--venvs-only` runs stage 2 alone, with no completion marker. `make
git-cleanup` and `make git-cleanup-apply` call it that way right after
`cleanup_git_state.py`, so every post-merge cleanup also reclaims the venvs
of the worktrees it just removed.

Run with both stages from a scheduler, a single lock file (`--lock-file`,
taken non-blockingly) keeps two runs from overlapping; a run that finds it
held skips cleanly rather than waiting. A single fixed-time daily trigger has
no retry if it fails, so a scheduler should invoke this hourly; a same-day
completion marker (`--state-file`) keeps the actual cleanup running at most
once per calendar day regardless of how often it fires.

Every deletion is appended to a dedicated hygiene log (`--log-file`). Both
files default to `~/.cache/transit-app/`.

Like `cleanup_git_state.py`, planning is the default; pass `--apply` to
actually delete anything.
"""

from __future__ import annotations

import argparse
import base64
import contextlib
import fcntl
import hashlib
import importlib.util
import io
import os
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
    from scripts.cleanup_git_state import CleanupError, PullRequest
else:
    PullRequest = cleanup_git_state.PullRequest
    CleanupError = cleanup_git_state.CleanupError

DEFAULT_LOCK_FILE = Path("/tmp/transit-git-hygiene.lock")
DEFAULT_STATE_DIR = Path.home() / ".cache" / "transit-app"
DEFAULT_LOG_FILE = DEFAULT_STATE_DIR / "git-hygiene.log"
DEFAULT_STATE_FILE = DEFAULT_STATE_DIR / "daily-git-hygiene-last-success"
DEFAULT_MIN_VENV_AGE_HOURS = 24
DEFAULT_MAX_VENV_DELETES_PER_RUN = 20
POETRY_COMMAND_TIMEOUT_SECONDS = 10
# pyproject.toml's `name`; Poetry names each of this project's venvs
# "<name>-<path hash>-py<major.minor>", so the glob matches only this repo's own
# venvs, never an unrelated project sharing the same virtualenvs.path.
POETRY_PROJECT_NAME = "transit-delay-app"
POETRY_VENV_GLOB = f"{POETRY_PROJECT_NAME}-*"


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
    from each other by exit code or output alone (both produce exit 1 with
    empty stdout AND empty stderr) -- "no venv created for this location
    yet" and "poetry itself failed for an unrelated, possibly transient
    reason". Callers that need to tell these apart must do so themselves;
    this function only ever reports "resolved" or "not resolved."

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
            timeout=POETRY_COMMAND_TIMEOUT_SECONDS,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if result.returncode != 0:
        return None
    path = result.stdout.strip()
    return Path(path).resolve() if path else None


def poetry_virtualenvs_path() -> Path:
    """Poetry's own `virtualenvs.path`: where every project venv on this host lives.

    Asking poetry, rather than defaulting to a platform-specific path, keeps the
    root right on both macOS and Linux. A failure raises instead of guessing,
    since a wrong root would silently enumerate nothing.
    """

    try:
        result = subprocess.run(
            ("poetry", "config", "virtualenvs.path"),
            capture_output=True,
            text=True,
            timeout=POETRY_COMMAND_TIMEOUT_SECONDS,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise HygieneError(f"could not run `poetry config virtualenvs.path` ({exc}); pass --venv-root") from exc
    path = result.stdout.strip()
    if result.returncode != 0 or not path:
        raise HygieneError("`poetry config virtualenvs.path` reported no path; pass --venv-root")
    return Path(path).expanduser()


def poetry_venv_name_prefix(location: Path) -> str:
    """The directory-name prefix Poetry gives every venv of the project at `location`.

    Mirrors Poetry's `EnvManager.generate_env_name`: the project name, then the
    first 8 characters of the URL-safe base64 SHA-256 of the directory's
    normcased real path. The interpreter's `<major>.<minor>` follows the
    returned `-py`, so one location can own several venvs.
    """

    normalized = os.path.normcase(os.path.realpath(location))
    digest = base64.urlsafe_b64encode(hashlib.sha256(normalized.encode()).digest()).decode()[:8]
    return f"{POETRY_PROJECT_NAME}-{digest}-py"


def primary_worktree(repo: Path) -> Path:
    """The main working tree of `repo`, which `git worktree list` always reports first."""

    worktrees = cleanup_git_state.parse_worktrees(
        cleanup_git_state.run_git(repo, "worktree", "list", "--porcelain").stdout
    )
    if not worktrees:
        raise HygieneError(f"`git worktree list` reported no worktrees for {repo}")
    return worktrees[0].path


def compute_in_use_poetry_venvs(repo: Path, main_venv: Path, *, venv_root: Path) -> set[Path]:
    """`main_venv` plus every venv under `venv_root` that a current worktree of `repo` owns.

    Ownership is read from Poetry's deterministic venv naming instead of asking
    `poetry env info` in each worktree. A worktree that never created a venv
    (the norm when `poetry run` is routed to the main checkout's environment)
    owns no candidate, and one whose venv exists owns it by name even when
    poetry cannot be spawned there. `main_venv`, resolved by poetry itself for
    `repo`, is the positive control: if its name does not carry the prefix
    computed for `repo`, Poetry's naming has changed, and no venv can be
    attributed safely.

    A `prunable` entry (its directory is gone and only git's administrative
    record remains) owns nothing. A locked worktree keeps its venvs even when
    its directory is absent, since locking exists to protect it from exactly
    this kind of cleanup.

    Assumes `repo` is the only clone of this project on the host: a second,
    independent clone's venvs carry its own path hash and look orphaned here.
    """

    expected_prefix = poetry_venv_name_prefix(repo)
    if not main_venv.name.startswith(expected_prefix):
        raise HygieneError(
            f"this checkout's own venv {main_venv.name!r} does not start with {expected_prefix!r}, the name "
            "Poetry's path hashing predicts for it; refusing to attribute any venv to a worktree"
        )
    worktrees = cleanup_git_state.parse_worktrees(
        cleanup_git_state.run_git(repo, "worktree", "list", "--porcelain").stdout
    )
    owned_prefixes = tuple(
        poetry_venv_name_prefix(worktree.path)
        for worktree in worktrees
        if not worktree.prunable and (worktree.locked or worktree.path.exists())
    )
    owned = {
        candidate.resolve()
        for candidate in venv_root.glob(POETRY_VENV_GLOB)
        if candidate.name.startswith(owned_prefixes)
    }
    return {main_venv, *owned}


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

    `repo` may be any worktree: the venv that anchors the naming check is
    resolved at the primary worktree, the one checkout that owns a real
    environment when worktrees route `poetry run` to it.

    `min_age_hours` is a second, independent safety margin on top of the
    ownership check: a venv younger than this is never deleted even if no
    current worktree owns it, in case a worktree was created (and its venv
    installed) after this run's own `git worktree list` snapshot but before
    this stage reached the apply loop.

    `max_deletes_per_run` is a circuit breaker, not a normal-operation limit.
    A plan exceeding this is treated as a signal something is systemically
    wrong (e.g. the ownership check itself misbehaving) and refuses to delete
    anything until a human looks, rather than silently deleting a large,
    irreversible batch. Checked (and printed) in dry-run mode too, so a
    preview never shows a plan that the real `--apply` run would then simply
    refuse.
    """

    print(f"== Orphaned poetry venv pruning ({venv_root}) ==")

    checkout = primary_worktree(repo)
    # Resolved exactly once here (not inside compute_in_use_poetry_venvs, and not
    # called a second time below) so a transient poetry hiccup on the later
    # re-check can never masquerade as "--venv-root is misconfigured".
    main_venv = poetry_env_path(checkout)

    # `venv_root.glob(...)` on a nonexistent directory simply yields nothing (no
    # error), so this one enumeration doubles as the "does --venv-root even
    # exist" check -- there's no separate is_dir() special case to keep in sync.
    now_epoch = time.time()
    candidates = sorted(path for path in venv_root.glob(POETRY_VENV_GLOB) if path.is_dir() and not path.is_symlink())

    if main_venv is None:
        # A clone where nothing ever ran poetry has no venv to anchor the
        # naming check, but then it also has nothing of this project's to prune.
        if not candidates:
            print(f"No {POETRY_VENV_GLOB} venv for this checkout or under {venv_root}; nothing to prune")
            return True
        raise HygieneError(
            f"could not resolve this checkout's own poetry venv at {checkout} (is `poetry` on PATH and "
            f"working?) although {venv_root} holds {len(candidates)} {POETRY_VENV_GLOB} venv(s); refusing to "
            "prune without the naming check that venv anchors"
        )

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

    in_use = compute_in_use_poetry_venvs(checkout, main_venv, venv_root=venv_root)
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
    # Re-read the in-use set once right before applying (not once per venv) to
    # catch a worktree created during planning. Only worth the extra `git
    # worktree list` when there's actually something to delete (delete_count == 0
    # already returned above).
    fresh_in_use = compute_in_use_poetry_venvs(checkout, main_venv, venv_root=venv_root)
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
    parser.add_argument("--repo", type=Path, default=Path("."), help="Any worktree in the target repo")
    parser.add_argument("--base", default="main", help="Up-to-date integration branch (default: main)")
    parser.add_argument("--remote", default="origin", help="Remote used for base validation")
    parser.add_argument("--protect", action="append", default=[], metavar="BRANCH", help="Extra local branch to retain")
    parser.add_argument("--apply", action="store_true", help="Apply the printed plans; default is a dry run")
    parser.add_argument(
        "--venvs-only",
        action="store_true",
        help="Run only orphaned venv pruning, with no once-per-day completion marker (the post-merge cleanup path)",
    )
    parser.add_argument("--lock-file", type=Path, default=DEFAULT_LOCK_FILE, help="Keeps two runs from overlapping")
    parser.add_argument("--log-file", type=Path, default=DEFAULT_LOG_FILE, help="Deletion log")
    parser.add_argument(
        "--state-file", type=Path, default=DEFAULT_STATE_FILE, help="Marks the last calendar day this job completed"
    )
    parser.add_argument(
        "--venv-root", type=Path, default=None, help="Directory to prune within (default: poetry's virtualenvs.path)"
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
    # something worth a log line every time. A --venvs-only run follows a
    # merge, which can happen several times a day, so it is never gated.
    if not args.venvs_only and already_succeeded_today(args.state_file, today=run_day):
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

        stages: list[tuple[str, Callable[[], bool]]] = []
        if not args.venvs_only:
            stages.append(
                (
                    "local cleanup",
                    lambda: run_local_cleanup(
                        repo,
                        base=args.base,
                        remote=args.remote,
                        protected=protected,
                        apply=args.apply,
                        log_file=log_file,
                    ),
                )
            )
        stages.append(
            (
                "orphaned venv pruning",
                lambda: run_orphaned_venv_pruning(
                    repo,
                    venv_root=args.venv_root if args.venv_root is not None else poetry_virtualenvs_path(),
                    min_age_hours=args.min_venv_age_hours,
                    max_deletes_per_run=args.max_venv_deletes_per_run,
                    apply=args.apply,
                    log_file=log_file,
                ),
            )
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
        if args.apply and not args.venvs_only and exit_code == 0:
            mark_succeeded_today(args.state_file, log_file, today=run_day)

        return exit_code
    finally:
        release_lock(lock_handle)


if __name__ == "__main__":
    raise SystemExit(main())
