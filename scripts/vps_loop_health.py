#!/usr/bin/env python3
"""Export `/vps-loop-run` health state and flag stuck/stale-pause conditions.

`NEXT_TASK.md` is `/vps-loop-run`'s own untracked input/status log (see
CLAUDE.md's Autonomous VPS loop section and `.claude/commands/vps-loop-run.md`'s
Step 0). The coordinator narrates every tick's outcome there, including its own
circuit-breaker pause bookkeeping (`**PAUSED ...**` / `**Still paused ...**` /
`**RESUMED ...**`) and a `**Blocker-tag:**` line on every tick that stops
without progress. Nothing outside a human reading that file surfaces those
facts today, so a stuck loop -- repeatedly hitting the same blocker, or paused
far longer than its own reduced probe cadence -- can go unnoticed for days
between manual checks.

This script parses the Status log (pure file reads -- no `git`/`gh` calls, so
it is cheap and safe to run every tick) and reports four health facts:

- `last_successful_tick`: the most recent entry that is neither a
  `Blocker-tag`-bearing stop nor a PAUSED-family bookkeeping-only entry.
- `current_item`: the item number the most recent entry names, falling back
  to the first backlog item without a terminal status marker
  (`DONE`/`MOOT`/`DO NOT START`) if no entry names one.
- `last_tick_outcome`: `"progress"` / `"idle"` / `"blocked"` / `"paused"` /
  `"unknown"` for the single most recent entry -- `deploy/vps/claude-loop.sh`
  uses this to decide whether to chain immediately into another tick
  (`"progress"`) or stop and back off (everything else).
- `blocker_class`: the tag behind the current stop, if the tick is currently
  blocked or the loop is currently paused (a `Still paused` bookkeeping line
  carries no tag of its own, so this looks back to the tag that caused the
  pause in the first place).
- `paused` / `paused_since`: whether the loop is currently in Step 0's
  circuit-breaker pause state, mirroring that step's own "am I currently
  paused?" check exactly: scan backward for the most recent PAUSED-family or
  `RESUMED` bookkeeping entry.

It also computes two alert flags:

- `repeated_without_progress`: the last 3 `Blocker-tag`-bearing entries,
  bounded by the most recent `RESUMED` exactly like Step 0's own streak
  window, share an identical tag -- the systemd-invoked tick keeps completing
  (exiting) without making progress, whether or not Step 0 has logged its own
  `PAUSED` bookkeeping line yet.
- `stale_pause`: the loop is currently paused and more wall-clock time has
  passed since the most recent PAUSED-family entry than its own reduced probe
  cadence allows (`--probe-multiplier` tick intervals, read from
  `deploy/systemd/claude-loop.timer`'s `OnCalendar`, plus `--stale-pause-
  buffer` slack for scheduling jitter) -- a probe should already have run and
  logged a fresh bookkeeping line by now.

`--format shell` emits `KEY='value'` lines safe to `eval` from a POSIX shell
(no `jq` dependency, unlike `--format json`, the default) -- `deploy/vps/
claude-loop.sh` uses this to fold the reported fields into its own heartbeat
dispatch's `client_payload`, so `vps-heartbeat-watchdog.yml` can alert on them
without any new external monitoring service.

Exit code: 0 normally, 1 if either alert flag is set, 2 on a hard error
(missing `NEXT_TASK.md`).
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import re
import shlex
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Sequence

# Reuse reconcile_next_task.py's already-reviewed backlog-item parser for the
# "current_item" fallback (first item with no terminal status marker) instead
# of duplicating its header/marker regexes. Loaded by file path since
# `scripts/` has no `__init__.py` and isn't guaranteed to be on `sys.path`
# (same approach as `daily_git_hygiene.py`/`reconcile_next_task.py`).
_SCRIPT_DIR = Path(__file__).resolve().parent
_RECONCILE_SPEC = importlib.util.spec_from_file_location("reconcile_next_task", _SCRIPT_DIR / "reconcile_next_task.py")
assert _RECONCILE_SPEC and _RECONCILE_SPEC.loader
reconcile_next_task = importlib.util.module_from_spec(_RECONCILE_SPEC)
sys.modules[_RECONCILE_SPEC.name] = reconcile_next_task
_RECONCILE_SPEC.loader.exec_module(reconcile_next_task)


class HealthError(RuntimeError):
    """Raised when the health report cannot be computed conservatively."""


STATUS_LOG_HEADING_RE = re.compile(r"^#\s+Status log\s*$")
# A real entry always opens at column 0 with "- <year>-...": continuation
# lines (including embedded "- <UTC timestamp>: ..." bullets quoted inside an
# entry's own prose) are always indented, so this never matches one of those.
# Deliberately loose past the year-month-day so one known historical entry
# whose timestamp itself wraps across several lines ("2026-09-10T~03:19Z
# (approximate -- ... ): **RESUMED ...") still counts as its own entry
# boundary instead of being silently merged into the previous entry's text --
# see `parse_entry`'s docstring for the (accepted) cost of that looseness.
ENTRY_BOUNDARY_RE = re.compile(r"^- (\d{4}-\d{2}-\d{2}T\S*)")
# The strict form used to actually extract a usable timestamp -- only ever
# applied to an entry's first line.
TIMESTAMP_RE = re.compile(r"^(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z)")
BLOCKER_TAG_RE = re.compile(r"\*\*Blocker-tag:\*\*\s+([a-z][a-z0-9-]*)")
# `**Blocker-tag:** none -- ...` is a documented convention for an explicitly
# non-tick-stopping note (see NEXT_TASK.md's item-56 entry); it must not be
# mistaken for a real recurring blocker class.
NO_TAG_VALUE = "none"
ITEM_MENTION_RE = re.compile(r"\bitems?\s+(\d+)\b", re.IGNORECASE)
PAUSED_RE = re.compile(r"^\*\*PAUSED\b")
STILL_PAUSED_RE = re.compile(r"^\*\*Still paused\b")
RESUMED_RE = re.compile(r"^\*\*RESUMED\b")
# Step 3's exact idle phrasing (`.claude/commands/vps-loop-run.md`) -- a
# substring match against an entry's full raw block, not just its first line,
# since it can appear after wrapped continuation text.
IDLE_TICK_TEXT = "nothing actionable this run."
ONCALENDAR_RE = re.compile(r"^\s*OnCalendar\s*=\s*(.+?)\s*$", re.MULTILINE)
# The only shape `deploy/systemd/claude-loop.timer` currently uses: every
# hour, at a fixed minute/second. A future timer using a different shape
# (e.g. a sub-hourly `*:0/20:00`) intentionally falls back to the caller's
# default rather than being guessed at here.
HOURLY_ONCALENDAR_RE = re.compile(r"^\*-\*-\*\s+\*:\d{1,2}:\d{2}$")
DEFAULT_TICK_INTERVAL_SECONDS = 3600


@dataclass(frozen=True)
class StatusEntry:
    """One `- <UTC timestamp>: ...` Status log entry, in file order."""

    raw: str
    timestamp: str | None
    kind: str  # "paused" | "still_paused" | "resumed" | "normal"
    blocker_tag: str | None
    item_number: int | None


def split_status_log_entries(text: str) -> list[str]:
    """Split the `# Status log` section into raw per-entry text blocks, in file order.

    One entry is everything from one `ENTRY_BOUNDARY_RE`-matching line up to
    (excluding) the next such line, exactly as `.claude/commands/vps-loop-
    run.md`'s Step 0 defines an entry. Text before the `# Status log` heading
    (the numbered backlog itself) is never scanned, so a backlog item's own
    prose can't accidentally look like a log entry.
    """

    lines = text.splitlines()
    start = 0
    for index, line in enumerate(lines):
        if STATUS_LOG_HEADING_RE.match(line):
            start = index + 1
            break
    else:
        return []

    boundaries = [index for index in range(start, len(lines)) if ENTRY_BOUNDARY_RE.match(lines[index])]
    blocks = []
    for position, boundary in enumerate(boundaries):
        end = boundaries[position + 1] if position + 1 < len(boundaries) else len(lines)
        blocks.append("\n".join(lines[boundary:end]))
    return blocks


def parse_entry(raw: str) -> StatusEntry:
    """Parse one raw entry block into its structured fields.

    `timestamp` is `None` for the one known historical shape where the
    timestamp itself is annotated and wraps across lines before its closing
    `): ` -- rare enough (one occurrence as of this writing, deep in
    history) that this only degrades that entry's own `timestamp` field
    rather than the (far more consequential) entry-boundary/blocker-tag/kind
    parsing, which all still work off the full block regardless. The same
    entry's `kind` also falls back to `"normal"` because the first line's
    `**RESUMED`/`**PAUSED`/`**Still paused` marker there is not immediately
    after the timestamp's own `: ` the way every other entry's is -- again
    accepted as a known, narrow residual rather than chased further, since it
    sits well before the log's tail and this module only ever reports on the
    most recent entries.
    """

    first_line = raw.splitlines()[0]
    ts_match = ENTRY_BOUNDARY_RE.match(first_line)
    assert ts_match
    strict_match = TIMESTAMP_RE.match(ts_match.group(1))
    timestamp = strict_match.group(1) if strict_match else None

    prefix_end = first_line.find(": ")
    remainder = first_line[prefix_end + 2 :] if prefix_end != -1 else ""
    if PAUSED_RE.match(remainder):
        kind = "paused"
    elif STILL_PAUSED_RE.match(remainder):
        kind = "still_paused"
    elif RESUMED_RE.match(remainder):
        kind = "resumed"
    else:
        kind = "normal"

    tag_match = BLOCKER_TAG_RE.search(raw)
    blocker_tag = tag_match.group(1) if tag_match and tag_match.group(1) != NO_TAG_VALUE else None

    item_match = ITEM_MENTION_RE.search(raw)
    item_number = int(item_match.group(1)) if item_match else None

    return StatusEntry(raw=raw, timestamp=timestamp, kind=kind, blocker_tag=blocker_tag, item_number=item_number)


def parse_status_log(text: str) -> list[StatusEntry]:
    """Parse every Status log entry, in file order."""

    return [parse_entry(block) for block in split_status_log_entries(text)]


def compute_pause_state(entries: Sequence[StatusEntry]) -> tuple[bool, StatusEntry | None]:
    """Mirror Step 0's own "am I currently paused?" check exactly.

    Scans backward for the most recent PAUSED-family (`paused`/
    `still_paused`) or `resumed` entry. None found, or the most recent one is
    `resumed` -> not paused. Otherwise -> paused, anchored at that entry
    (its own timestamp is `paused_since`).
    """

    for entry in reversed(entries):
        if entry.kind in ("paused", "still_paused"):
            return True, entry
        if entry.kind == "resumed":
            return False, None
    return False, None


def compute_recent_blocker_tags(entries: Sequence[StatusEntry]) -> list[str]:
    """The most recent (up to 3) `Blocker-tag`s, most-recent-first.

    Bounded by the most recent `resumed` entry, exactly like Step 0's own
    streak window (a `RESUMED` genuinely resets the streak, so a tag from
    before it must never count again).
    """

    tags: list[str] = []
    for entry in reversed(entries):
        if entry.kind == "resumed":
            break
        if entry.blocker_tag:
            tags.append(entry.blocker_tag)
        if len(tags) == 3:
            break
    return tags


def compute_repeated_without_progress(recent_tags: Sequence[str]) -> bool:
    """True if the last 3 tag-bearing entries in the current window share an identical tag.

    This is the exact condition under which Step 0 itself would pause (or
    already has) -- computed independently here so it fires even on a tick
    that crashed hard enough to never reach Step 0's own bookkeeping write.
    """

    return len(recent_tags) == 3 and len(set(recent_tags)) == 1


def compute_blocker_class(entries: Sequence[StatusEntry], *, paused: bool) -> str | None:
    """The tag behind the loop's current stop, or `None` if it isn't currently stopped.

    The very last entry's own tag wins if it has one (the tick just stopped
    on a blocker). Otherwise, if the loop is paused, a bookkeeping-only
    `Still paused` entry carries no tag of its own -- look back for the most
    recent real tag, which is the one that caused the pause.
    """

    if not entries:
        return None
    if entries[-1].blocker_tag:
        return entries[-1].blocker_tag
    if paused:
        for entry in reversed(entries):
            if entry.blocker_tag:
                return entry.blocker_tag
    return None


def compute_last_successful_tick(entries: Sequence[StatusEntry]) -> str | None:
    """The most recent entry that is neither a blocked stop nor pause bookkeeping."""

    for entry in reversed(entries):
        if entry.kind in ("paused", "still_paused"):
            continue
        if entry.blocker_tag:
            continue
        if entry.timestamp:
            return entry.timestamp
    return None


def compute_last_tick_outcome(entries: Sequence[StatusEntry]) -> str:
    """Classify the most recent Status log entry for `deploy/vps/claude-loop.sh`'s chain gate.

    One of:
    - `"progress"`: the tick shipped something, or otherwise ended normally with
      no blocker and no idle marker (e.g. an item-skip entry) -- worth chaining
      into another tick immediately rather than waiting for the next scheduled
      invocation.
    - `"idle"`: Step 3's "nothing actionable this run" -- the backlog is fully
      claimed/blocked/DO-NOT-START; chaining again immediately would just
      reproduce the same idle result, so the caller should stop and apply its
      own (gentler, but still bounded) backoff.
    - `"blocked"`: the entry carries its own `Blocker-tag` -- a genuine
      failure/blocker per Step 0; the caller should stop and back off.
    - `"paused"`: the entry is itself PAUSED-family bookkeeping (Step 0's
      circuit breaker already tripped) -- stop and back off exactly like
      `"blocked"`.
    - `"unknown"`: no entries at all (fresh/empty Status log), or the last
      entry is a bare `RESUMED` marker with no follow-on outcome yet (only
      possible if the process died between logging `RESUMED` and logging that
      attempt's own result) -- treat as ambiguous, same stop-and-backoff
      handling as `"blocked"`.

    Only the single most recent entry is examined: one external tick can log
    several entries (e.g. Step 3b/2b item-skip lines before a final dispatch),
    but by construction the last one written is always that tick's own
    terminal outcome -- the same assumption `compute_current_item` and
    `compute_blocker_class` already rely on.
    """

    if not entries:
        return "unknown"
    last = entries[-1]
    if last.kind in ("paused", "still_paused"):
        return "paused"
    if last.kind == "resumed":
        return "unknown"
    if last.blocker_tag:
        return "blocked"
    if IDLE_TICK_TEXT in last.raw:
        return "idle"
    return "progress"


def compute_current_item(entries: Sequence[StatusEntry], backlog_lines: Sequence[str]) -> int | None:
    """The item the loop is currently on: the most recent entry that names one.

    Falls back to the first backlog item with no terminal status marker
    (`DONE`/`MOOT`/`DO NOT START`) if no entry names one at all (e.g. right
    after a fresh checkout with an empty Status log) -- a best-effort proxy
    for "what's next" that, unlike Step 3's own item selection, has no `gh`
    access to skip an item with an in-flight PR.
    """

    for entry in reversed(entries):
        if entry.item_number is not None:
            return entry.item_number
    for item in reconcile_next_task.parse_items(backlog_lines):
        header = backlog_lines[item.line_index]
        if not reconcile_next_task.has_terminal_marker(header):
            return item.number
    return None


def parse_hourly_tick_interval_seconds(timer_text: str) -> int | None:
    """Read `deploy/systemd/claude-loop.timer`'s `OnCalendar` as a tick interval, in seconds.

    Only recognizes the one shape currently in use (fixed hourly). Returns
    `None` for anything else so the caller can fall back to an explicit
    override or a documented default instead of silently guessing wrong.
    """

    match = ONCALENDAR_RE.search(timer_text)
    if not match:
        return None
    if HOURLY_ONCALENDAR_RE.match(match.group(1)):
        return 3600
    return None


def compute_stale_pause(
    *,
    paused: bool,
    paused_entry: StatusEntry | None,
    now: datetime,
    reduced_cadence_seconds: float,
    buffer_multiplier: float,
) -> bool:
    """True if the loop has been paused longer than its own reduced probe cadence allows.

    A probe attempt (Step 0's "a probe attempt IS a normal, full,
    unrestricted run of Steps 2 onward") should log a fresh `Still paused`/
    `RESUMED` bookkeeping entry roughly every reduced-cadence interval once
    paused; `buffer_multiplier` adds slack for ordinary scheduling jitter
    before treating a longer silence as suspicious.
    """

    if not paused or paused_entry is None or paused_entry.timestamp is None:
        return False
    paused_at = datetime.strptime(paused_entry.timestamp, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    elapsed_seconds = (now - paused_at).total_seconds()
    return elapsed_seconds > reduced_cadence_seconds * buffer_multiplier


def build_report(
    *,
    next_task_path: Path,
    timer_path: Path,
    tick_interval_seconds: int | None,
    probe_multiplier: float,
    stale_pause_buffer: float,
    now: datetime | None = None,
) -> dict[str, object]:
    """Compute the full health report as a JSON-serializable dict."""

    now = now or datetime.now(timezone.utc)
    text = next_task_path.read_text(encoding="utf-8")
    lines = text.splitlines(keepends=True)
    entries = parse_status_log(text)

    paused, paused_entry = compute_pause_state(entries)
    recent_tags = compute_recent_blocker_tags(entries)
    repeated_without_progress = compute_repeated_without_progress(recent_tags)
    blocker_class = compute_blocker_class(entries, paused=paused)
    last_successful_tick = compute_last_successful_tick(entries)
    current_item = compute_current_item(entries, lines)
    last_tick_outcome = compute_last_tick_outcome(entries)

    interval = tick_interval_seconds
    interval_source = "override"
    if interval is None:
        timer_text = timer_path.read_text(encoding="utf-8") if timer_path.exists() else ""
        interval = parse_hourly_tick_interval_seconds(timer_text)
        interval_source = "parsed"
        if interval is None:
            interval = DEFAULT_TICK_INTERVAL_SECONDS
            interval_source = "default"

    reduced_cadence_seconds = interval * probe_multiplier
    stale_pause = compute_stale_pause(
        paused=paused,
        paused_entry=paused_entry,
        now=now,
        reduced_cadence_seconds=reduced_cadence_seconds,
        buffer_multiplier=stale_pause_buffer,
    )

    return {
        "last_successful_tick": last_successful_tick,
        "current_item": current_item,
        "last_tick_outcome": last_tick_outcome,
        "blocker_class": blocker_class,
        "paused": paused,
        "paused_since": paused_entry.timestamp if paused_entry else None,
        "tick_interval_seconds": interval,
        "tick_interval_source": interval_source,
        "reduced_probe_cadence_seconds": reduced_cadence_seconds,
        "recent_blocker_tags": recent_tags,
        "alerts": {
            "repeated_without_progress": repeated_without_progress,
            "stale_pause": stale_pause,
        },
    }


def format_shell(report: dict[str, object]) -> str:
    """Render the report as `KEY='value'` lines safe to `eval` from a POSIX shell."""

    def scalar(value: object) -> str:
        if value is None:
            return ""
        if isinstance(value, bool):
            return "true" if value else "false"
        return str(value)

    alerts = report["alerts"]
    assert isinstance(alerts, dict)
    fields = {
        "VPS_LOOP_LAST_SUCCESSFUL_TICK": report["last_successful_tick"],
        "VPS_LOOP_CURRENT_ITEM": report["current_item"],
        "VPS_LOOP_LAST_TICK_OUTCOME": report["last_tick_outcome"],
        "VPS_LOOP_BLOCKER_CLASS": report["blocker_class"],
        "VPS_LOOP_PAUSED": report["paused"],
        "VPS_LOOP_PAUSED_SINCE": report["paused_since"],
        "VPS_LOOP_REPEATED_WITHOUT_PROGRESS": alerts["repeated_without_progress"],
        "VPS_LOOP_STALE_PAUSE": alerts["stale_pause"],
    }
    return "".join(f"{name}={shlex.quote(scalar(value))}\n" for name, value in fields.items())


def main(argv: Sequence[str] | None = None) -> int:
    """CLI entry point."""

    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--repo", type=Path, default=Path.cwd(), help="Any worktree in the target repository")
    parser.add_argument("--file", type=Path, default=None, help="Path to NEXT_TASK.md (default: <repo>/NEXT_TASK.md)")
    parser.add_argument(
        "--timer-file",
        type=Path,
        default=None,
        help="systemd timer unit to derive the tick interval from (default: <repo>/deploy/systemd/claude-loop.timer)",
    )
    parser.add_argument(
        "--tick-interval-seconds",
        type=int,
        default=None,
        help="Override the tick interval instead of parsing --timer-file",
    )
    parser.add_argument(
        "--probe-multiplier",
        type=float,
        default=3.0,
        help="Reduced probe cadence = this many tick intervals (matches vps-loop-run.md Step 0)",
    )
    parser.add_argument(
        "--stale-pause-buffer",
        type=float,
        default=1.5,
        help="Extra slack, as a multiple of the reduced probe cadence, before stale_pause fires",
    )
    parser.add_argument("--format", choices=("json", "shell"), default="json")
    parser.add_argument(
        "--out", type=Path, default=None, help="Also write the report as JSON to this path, regardless of --format"
    )
    args = parser.parse_args(argv)

    repo = args.repo.resolve()
    next_task_path = (args.file or repo / "NEXT_TASK.md").resolve()
    timer_path = (args.timer_file or repo / "deploy" / "systemd" / "claude-loop.timer").resolve()

    if not next_task_path.exists():
        print(f"ERROR: {next_task_path} does not exist", file=sys.stderr)
        return 2

    try:
        report = build_report(
            next_task_path=next_task_path,
            timer_path=timer_path,
            tick_interval_seconds=args.tick_interval_seconds,
            probe_multiplier=args.probe_multiplier,
            stale_pause_buffer=args.stale_pause_buffer,
        )
    except (HealthError, OSError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2

    if args.out is not None:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")

    if args.format == "shell":
        sys.stdout.write(format_shell(report))
    else:
        print(json.dumps(report, indent=2))

    alerts = report["alerts"]
    assert isinstance(alerts, dict)
    return 1 if any(alerts.values()) else 0


if __name__ == "__main__":
    raise SystemExit(main())
