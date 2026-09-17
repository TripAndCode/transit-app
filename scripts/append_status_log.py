#!/usr/bin/env python3
"""Append one entry to `NEXT_TASK.md`'s `# Status log`, under a crash-safe advisory lock.

`NEXT_TASK.md` is `/vps-loop-run`'s own untracked input/status log (see
CLAUDE.md's Autonomous VPS loop section and `.claude/commands/vps-loop-run.md`).
Every step of that coordinator that logs an outcome does so by appending a
`- <UTC timestamp>: ...` bullet to the Status log. Before this script existed,
that append was always a direct file edit with no mutual exclusion: an
interactive `/vps-loop-run` session and the hourly cron-triggered
`claude-loop.service` tick (`deploy/vps/claude-loop.sh`) could both read the
log's tail, each independently decide what to append, and both write, with
nothing stopping either. `deploy/vps/claude-loop.sh` takes an exclusive
`flock` on `/tmp/claude-loop.lock` before running a tick, but that only
prevents two *cron-triggered* ticks from overlapping -- an interactive
session run by hand never acquired it, and even if it tried to acquire that
exact same lock it would be reaching for the wrong tool (see "Why a separate
lock file" below).

This script is the fix: every Status log append -- from any caller, cron or
interactive -- goes through here, which wraps the read-then-append step in a
dedicated advisory lock. It also generates the entry's own timestamp itself,
at the moment it actually acquires the lock and writes, rather than trusting
a timestamp the caller computed earlier and that may have gone stale waiting
on a lock. Since two lock holders can never write concurrently and each
records the true time of its own write, entries land in strictly
non-decreasing timestamp order by construction -- closing not just the
concurrent-write hazard but the specific `out_of_order_tail` symptom
`scripts/vps_loop_health.py` watches for.

## Why a separate lock file, not `/tmp/claude-loop.lock` itself

`deploy/vps/claude-loop.sh` opens `/tmp/claude-loop.lock` on fd 200 and holds
an exclusive `flock` on it for an entire tick, including the `claude -p
"/vps-loop-run"` subprocess it runs synchronously in the foreground. Bash
file descriptors are inherited across `fork`+`exec` by default, so that
subprocess -- and anything it spawns in turn, including this script when the
coordinator invokes it under cron -- inherits fd 200 already pointing at the
locked file. `flock(2)` locks are per *open file description*, not per
inode or per process: if this script opened `/tmp/claude-loop.lock` fresh
and tried to `flock` it, that would be an independent open file description
contending against the one the ancestor process already holds, and would
self-deadlock every cron-triggered tick forever (waiting on a lock its own
process tree already holds and will only release once this script itself
returns). Using a distinct file (`DEFAULT_LOCK_FILE` below) for this
narrower, per-append critical section avoids that trap entirely while still
serializing against a concurrent interactive session, which never holds
`/tmp/claude-loop.lock` at all.

## Why a real lock, not a PID-file or lock-free retry protocol

A PID+timestamp lock file needs its own staleness detection (a crash must not
wedge every future append behind a lock nobody will ever release) --
`scripts/vps_loop_chain_state.py` already carries that complexity for a
different purpose. `flock` needs none of that: it is tied to the lifetime of
an open file descriptor, so the kernel releases it automatically the instant
the holding process exits for any reason (clean exit, crash, `SIGKILL`), with
no separate staleness bookkeeping required. A lock-free "re-read the tail,
write, and retry once on mismatch" protocol was also considered (the
"lock-free append protocol" candidate for this problem) but was rejected: an
interactive coordinator session already invokes each Status log append as its
own short-lived process (one Bash tool call), which is exactly the shape a
short-lived helper needs to hold a real lock for just the read-then-append
window -- there is no obstacle here that would force falling back to
optimistic concurrency instead.

## Closing the duplicate-idle-tail race specifically

A plain write-level lock alone still leaves one documented race open: Step
3's idle-throttle rule ("if the Status log's last entry already reads
'nothing actionable this run.', don't log a second one") reads the tail
*before* deciding whether to append at all. Two callers can each read a
not-yet-idle tail, each decide to append the idle marker, and then simply
take turns writing it under the lock -- producing exactly the
`duplicate_idle_tail` symptom `vps_loop_health.py` detects, even though no
write was ever corrupted or interleaved. `--skip-if-tail-startswith` closes
this by moving the check itself inside the locked section: the caller passes
the text it believes would trigger the skip, and this script re-reads the
current tail under the lock immediately before writing and honors the skip
against that fresh read, not the caller's now-possibly-stale one.

Exit codes: 0 on success (appended, or skipped via `--skip-if-tail-startswith`
matching -- both are normal outcomes, not errors), 2 on a usage or file
error, 3 if the lock could not be acquired within the timeout.
"""

from __future__ import annotations

import argparse
import contextlib
import fcntl
import importlib.util
import json
import os
import re
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator, Sequence

# Reuse vps_loop_health.py's already-reviewed Status log heading/entry
# parsing instead of duplicating its regexes. Loaded by file path since
# `scripts/` has no `__init__.py` and isn't guaranteed to be on `sys.path`
# (same approach that module itself uses for reconcile_next_task.py).
_SCRIPT_DIR = Path(__file__).resolve().parent
_HEALTH_SPEC = importlib.util.spec_from_file_location("vps_loop_health", _SCRIPT_DIR / "vps_loop_health.py")
assert _HEALTH_SPEC and _HEALTH_SPEC.loader
vps_loop_health = importlib.util.module_from_spec(_HEALTH_SPEC)
sys.modules[_HEALTH_SPEC.name] = vps_loop_health
_HEALTH_SPEC.loader.exec_module(vps_loop_health)

DEFAULT_LOCK_FILE = Path(os.environ.get("CLAUDE_LOOP_STATUS_LOG_LOCK_FILE", "/tmp/claude-loop-status-log.lock"))
DEFAULT_LOCK_TIMEOUT_SECONDS = 60.0
LOCK_POLL_INTERVAL_SECONDS = 0.05
TIMESTAMP_FORMAT = "%Y-%m-%dT%H:%M:%SZ"
# Matches a top-level `# Heading` line but never a `## Subheading` (the second
# character must be a space, not another `#`) -- used only to find where the
# Status log section *ends* (the next top-level heading, if any exist after
# it). `vps_loop_health.STATUS_LOG_HEADING_RE` finds where it *starts*.
TOP_LEVEL_HEADING_RE = re.compile(r"^# ")


class AppendStatusLogError(RuntimeError):
    """Raised when the append cannot be performed conservatively."""


class LockTimeoutError(RuntimeError):
    """Raised when the advisory lock could not be acquired within the timeout."""


@contextlib.contextmanager
def held_lock(
    lock_path: Path,
    *,
    timeout_seconds: float,
    poll_interval_seconds: float = LOCK_POLL_INTERVAL_SECONDS,
) -> Iterator[None]:
    """Hold an exclusive advisory lock on `lock_path` for the duration of the `with` block.

    Blocks (polling `LOCK_NB` rather than a bare blocking `flock`, so a
    timeout can be enforced) until acquired or `timeout_seconds` elapses. The
    lock is released automatically -- by the kernel, not by any bookkeeping
    this function does -- the instant the holding process's file descriptor
    closes, including on a crash, so a killed holder can never leave a
    permanent deadlock for the next caller.
    """

    lock_path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(str(lock_path), os.O_RDWR | os.O_CREAT, 0o644)
    deadline = time.monotonic() + timeout_seconds
    try:
        while True:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    raise LockTimeoutError(
                        f"could not acquire {lock_path} within {timeout_seconds}s "
                        "-- another Status log append is likely in progress"
                    ) from None
                time.sleep(poll_interval_seconds)
        try:
            yield
        finally:
            fcntl.flock(fd, fcntl.LOCK_UN)
    finally:
        os.close(fd)


def find_status_log_section_bounds(lines: Sequence[str]) -> tuple[int, int]:
    """Return `(heading_index, end_index)` for the `# Status log` section.

    `end_index` is the index of the next top-level (`# `) heading after it,
    or `len(lines)` if the Status log is the last section -- true of
    `NEXT_TASK.md` today, but this stays correct even if that ever changes.
    """

    heading_index = None
    for index, line in enumerate(lines):
        if vps_loop_health.STATUS_LOG_HEADING_RE.match(line):
            heading_index = index
            break
    if heading_index is None:
        raise AppendStatusLogError("no '# Status log' heading found")

    end_index = len(lines)
    for index in range(heading_index + 1, len(lines)):
        if TOP_LEVEL_HEADING_RE.match(lines[index]):
            end_index = index
            break
    return heading_index, end_index


def format_entry_block(entry_text: str, *, timestamp: str) -> str:
    """Render one Status log entry, indenting any continuation lines by 2 spaces.

    Matches the convention already used throughout `NEXT_TASK.md`'s Status
    log: only the first line carries the `- <timestamp>: ` prefix, and a
    caller-supplied continuation line that doesn't already start with
    whitespace gets indented so it reads as part of the same entry rather
    than a new top-level bullet.
    """

    body_lines = entry_text.splitlines() or [""]
    first, *rest = body_lines
    formatted = [f"- {timestamp}: {first}"]
    for line in rest:
        if line == "" or line[:1].isspace():
            formatted.append(line)
        else:
            formatted.append(f"  {line}")
    return "\n".join(formatted)


def last_entry_first_line_remainder(text: str) -> str | None:
    """The most recent Status log entry's text after its `<timestamp>: ` prefix, or `None` if empty."""

    blocks = vps_loop_health.split_status_log_entries(text)
    if not blocks:
        return None
    first_line = blocks[-1].splitlines()[0]
    prefix_end = first_line.find(": ")
    return first_line[prefix_end + 2 :] if prefix_end != -1 else ""


def _atomic_write(path: Path, content: str) -> None:
    fd, tmp_name = tempfile.mkstemp(dir=str(path.parent), prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(content)
        os.replace(tmp_name, path)
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(tmp_name)
        raise


def append_entry(
    next_task_path: Path,
    entry_text: str,
    *,
    now: datetime,
    skip_if_tail_startswith: str | None = None,
) -> tuple[bool, str]:
    """Append `entry_text` (or skip) to `next_task_path`'s Status log. Caller must hold the lock.

    Returns `(appended, entry_block_or_skip_reason)`. When
    `skip_if_tail_startswith` is given and the *current* last entry's text
    (read fresh, right now, under the lock) already starts with it, nothing
    is written and `appended` is `False` -- this is what closes the
    duplicate-idle-tail race: the check happens against the freshest possible
    read, immediately before the write it would otherwise gate, not against
    whatever the caller last saw before requesting the lock.
    """

    original = next_task_path.read_text(encoding="utf-8")

    if skip_if_tail_startswith is not None:
        remainder = last_entry_first_line_remainder(original)
        if remainder is not None and remainder.startswith(skip_if_tail_startswith):
            return False, remainder

    lines = original.splitlines()
    _heading_index, end_index = find_status_log_section_bounds(lines)
    timestamp = now.strftime(TIMESTAMP_FORMAT)
    entry_block = format_entry_block(entry_text, timestamp=timestamp)
    new_lines = lines[:end_index] + entry_block.splitlines() + lines[end_index:]
    _atomic_write(next_task_path, "\n".join(new_lines) + "\n")
    return True, entry_block


def run(
    *,
    next_task_path: Path,
    entry_text: str,
    lock_path: Path,
    lock_timeout_seconds: float,
    now: datetime | None = None,
    skip_if_tail_startswith: str | None = None,
) -> dict[str, object]:
    """Acquire the lock, append (or skip), and return a JSON-serializable result."""

    if not next_task_path.exists():
        raise AppendStatusLogError(f"{next_task_path} does not exist")

    with held_lock(lock_path, timeout_seconds=lock_timeout_seconds):
        now = now or datetime.now(timezone.utc)
        appended, detail = append_entry(
            next_task_path,
            entry_text,
            now=now,
            skip_if_tail_startswith=skip_if_tail_startswith,
        )

    if appended:
        return {"appended": True, "entry": detail, "path": str(next_task_path)}
    return {"appended": False, "reason": "tail already matches skip-if-tail-startswith", "current_tail": detail}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--repo", type=Path, default=Path.cwd(), help="Any worktree in the target repository")
    parser.add_argument("--file", type=Path, default=None, help="Path to NEXT_TASK.md (default: <repo>/NEXT_TASK.md)")
    entry_group = parser.add_mutually_exclusive_group(required=True)
    entry_group.add_argument("--entry", help="Entry text, i.e. everything after the '- <timestamp>: ' prefix")
    entry_group.add_argument("--entry-file", type=Path, help="Read entry text from this file instead of --entry")
    parser.add_argument(
        "--skip-if-tail-startswith",
        default=None,
        help="Do not append if the current last entry's own text already starts with this",
    )
    parser.add_argument("--lock-file", type=Path, default=DEFAULT_LOCK_FILE)
    parser.add_argument("--lock-timeout-seconds", type=float, default=DEFAULT_LOCK_TIMEOUT_SECONDS)
    parser.add_argument(
        "--now", default=None, help="Override the current time (UTC, %%Y-%%m-%%dT%%H:%%M:%%SZ); testing only"
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    repo = args.repo.resolve()
    next_task_path = (args.file or repo / "NEXT_TASK.md").resolve()
    entry_text = args.entry if args.entry is not None else args.entry_file.read_text(encoding="utf-8").rstrip("\n")
    now = datetime.strptime(args.now, TIMESTAMP_FORMAT).replace(tzinfo=timezone.utc) if args.now else None

    try:
        result = run(
            next_task_path=next_task_path,
            entry_text=entry_text,
            lock_path=args.lock_file,
            lock_timeout_seconds=args.lock_timeout_seconds,
            now=now,
            skip_if_tail_startswith=args.skip_if_tail_startswith,
        )
    except LockTimeoutError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 3
    except (AppendStatusLogError, OSError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2

    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
