#!/usr/bin/env python3
"""Guarded-continuation state for `deploy/vps/claude-loop.sh`'s chained ticks.

Previously one systemd-timer firing ran exactly one `/vps-loop-run` tick, so a
successful merge/cleanup still had to wait for the next scheduled firing (up
to a full timer interval) before the loop could pick up the next actionable
backlog item. This module lets the wrapper script chain several ticks back to
back within a single invocation whenever a tick makes real progress, while
stopping and backing off whenever it doesn't -- without needing a second
Claude process or a shorter timer cadence.

The state this module persists (as small JSON at a path the wrapper controls,
analogous to `vps_loop_health.py`'s own `--out` report) is deliberately
separate from `NEXT_TASK.md`'s own Status log and Step 0 circuit breaker:
that log is the coordinator's own narrative of *why* a tick stopped, read and
written entirely by the `claude` process itself. This module instead tracks
the *wrapper's* own bookkeeping -- is a tick currently in flight, how many
consecutive ticks in a row made no progress, and when the next attempt is
allowed -- so the shell wrapper can decide "chain again right now" vs. "stop
and wait" without parsing prose.

Four CLI subcommands. `gate`, `begin`, and `record-outcome` are the ones the
wrapper calls around every tick, each emitting `KEY='value'` shell-eval lines
like `vps_loop_health.py --format shell`:

- `gate`: may a new tick start right now? Also recovers a stale `in_progress`
  flag left behind by a tick that crashed (or was killed on its own timeout)
  before it could record an outcome.
- `begin`: mark a tick as in flight (call immediately before invoking `claude`).
- `record-outcome`: given the tick's own outcome (`vps_loop_health.py`'s
  `last_tick_outcome`, or `"unknown"` if the `claude` invocation itself exited
  non-zero), clear the in-flight flag and decide `continue` vs. `stop`.

A fourth, `show`, is a read-only inspector for manual/ops use (not called by
the wrapper itself): it prints the current state, in the same `KEY='value'`
shell form under `--format shell` or as JSON (the default).

Exit code: 0 normally; `gate` additionally exits 1 when not currently allowed
to run (mirroring `vps_loop_health.py`'s "1 means look closer" convention),
2 on a hard error.
"""

from __future__ import annotations

import argparse
import json
import os
import shlex
import sys
import tempfile
from dataclasses import asdict, dataclass, replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Sequence

TIMESTAMP_FORMAT = "%Y-%m-%dT%H:%M:%SZ"

# A tick that made real forward progress (`vps_loop_health.py`'s `"progress"`)
# is the only outcome worth chaining into another tick immediately. Every
# other outcome stops the chain for this invocation.
CONTINUE_OUTCOMES = frozenset({"progress"})
KNOWN_OUTCOMES = frozenset({"progress", "idle", "blocked", "paused", "unknown"})


@dataclass(frozen=True)
class ChainState:
    """Persisted wrapper-level bookkeeping. See the module docstring for scope."""

    in_progress: bool = False
    pid: int | None = None
    started_at: str | None = None
    consecutive_non_progress: int = 0
    next_earliest_attempt: str | None = None
    last_outcome: str | None = None
    last_updated: str | None = None

    def to_json_dict(self) -> dict[str, object]:
        return asdict(self)

    @staticmethod
    def from_json_dict(data: dict[str, object]) -> "ChainState":
        # Only ever read fields this version knows about -- an older/newer
        # state file with extra keys degrades gracefully instead of erroring,
        # and a missing key falls back to the dataclass default.
        known_fields = set(ChainState.__dataclass_fields__)
        filtered: dict[str, Any] = {key: value for key, value in data.items() if key in known_fields}
        return ChainState(**filtered)


def format_timestamp(moment: datetime) -> str:
    return moment.astimezone(timezone.utc).strftime(TIMESTAMP_FORMAT)


def parse_timestamp(value: str) -> datetime:
    return datetime.strptime(value, TIMESTAMP_FORMAT).replace(tzinfo=timezone.utc)


def load_state(path: Path) -> ChainState:
    """Load state from `path`, defaulting to a fresh, never-run state.

    A missing file (first run ever) is not an error. A present-but-corrupt
    file (partial write from a hard kill) is treated the same way rather than
    aborting the loop over its own bookkeeping -- the worst case is one extra
    tick running without the benefit of prior backoff/stale-lock history,
    which is far safer than refusing to run at all.
    """

    try:
        raw = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return ChainState()
    try:
        data = json.loads(raw)
        if not isinstance(data, dict):
            raise ValueError("chain state file must contain a JSON object")
        return ChainState.from_json_dict(data)
    except (json.JSONDecodeError, ValueError):
        return ChainState()


def save_state(path: Path, state: ChainState) -> None:
    """Write `state` to `path` atomically (write to a temp file, then rename)."""

    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(dir=str(path.parent), prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(state.to_json_dict(), handle, indent=2)
            handle.write("\n")
        os.replace(tmp_name, path)
    except BaseException:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise


def default_pid_alive(pid: int) -> bool:
    """Best-effort liveness check via `os.kill(pid, 0)` (no signal actually sent)."""

    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        # Process exists but is owned by someone else -- still alive.
        return True
    except OSError:
        return True
    return True


PidAliveFn = Callable[[int], bool]


def is_stale(state: ChainState, *, now: datetime, max_age_seconds: float, pid_alive_fn: PidAliveFn) -> bool:
    """True if `state` claims a tick is in flight that can no longer actually be running.

    A tick is only ever in flight for the wall-clock duration of one `claude`
    invocation, which `deploy/vps/claude-loop.sh` itself caps at
    `CLAUDE_TICK_TIMEOUT_SEC` -- so `in_progress` still `True` well past that
    ceiling, or naming a pid that's no longer alive, can only mean the
    previous wrapper process was killed (systemd's own `TimeoutStartSec`, an
    OOM kill, a VPS reboot) before it reached `record-outcome`. Recovering
    from that is what keeps a single hard kill from wedging every future tick
    behind a flag that will never clear itself.
    """

    if not state.in_progress:
        return False
    if state.pid is not None and not pid_alive_fn(state.pid):
        return True
    if state.started_at is None:
        # in_progress with no recorded start time can't be aged -- treat as
        # stale outright rather than trusting a flag with no way to expire.
        return True
    age_seconds = (now - parse_timestamp(state.started_at)).total_seconds()
    return age_seconds > max_age_seconds


def recover_if_stale(
    state: ChainState,
    *,
    now: datetime,
    max_age_seconds: float,
    base_seconds: float = 300.0,
    cap_seconds: float = 3600.0,
    pid_alive_fn: PidAliveFn = default_pid_alive,
) -> tuple[ChainState, bool]:
    """Clear a stale in-flight flag, folding the crash itself in as a non-progress outcome.

    A crash mid-tick is exactly as much "no progress" as a reported blocker --
    it still counts toward the backoff schedule (`record_outcome`'s own
    escalation), so a wrapper that keeps getting killed mid-tick still slows
    itself down instead of retrying at full speed forever. `base_seconds`/
    `cap_seconds` are threaded through to that same `record_outcome` call so a
    recovered crash honors the operator's configured backoff schedule instead
    of silently reverting to `record_outcome`'s own defaults.
    """

    if not is_stale(state, now=now, max_age_seconds=max_age_seconds, pid_alive_fn=pid_alive_fn):
        return state, False
    recovered_state, _ = record_outcome(
        replace(state, in_progress=False, pid=None, started_at=None),
        outcome="unknown",
        now=now,
        base_seconds=base_seconds,
        cap_seconds=cap_seconds,
    )
    return recovered_state, True


def gate(state: ChainState, *, now: datetime) -> tuple[bool, str]:
    """May a new tick start right now? Assumes any stale `in_progress` was already recovered."""

    if state.in_progress:
        return False, "a tick is already recorded in-flight"
    if state.next_earliest_attempt is not None:
        earliest = parse_timestamp(state.next_earliest_attempt)
        if now < earliest:
            return False, f"backing off until {state.next_earliest_attempt} (last outcome: {state.last_outcome})"
    return True, "ok"


def begin(state: ChainState, *, pid: int, now: datetime) -> ChainState:
    """Mark a tick as in flight. Call immediately before invoking `claude`."""

    return replace(
        state,
        in_progress=True,
        pid=pid,
        started_at=format_timestamp(now),
        last_updated=format_timestamp(now),
    )


def compute_backoff_seconds(*, consecutive_non_progress: int, base_seconds: float, cap_seconds: float) -> float:
    """Exponential backoff, doubling per consecutive non-progress tick, capped at `cap_seconds`."""

    if consecutive_non_progress <= 0:
        return 0.0
    # Clamp the exponent before raising 2 to it: an astronomically large
    # `consecutive_non_progress` would otherwise risk `OverflowError` computing
    # `2 ** exponent` (or converting it to float) long before any realistic
    # backoff schedule would ever reach that many consecutive non-progress
    # ticks -- the cap below already makes any larger exponent behave
    # identically anyway.
    exponent = min(consecutive_non_progress - 1, 64)
    return min(base_seconds * (2**exponent), cap_seconds)


def record_outcome(
    state: ChainState,
    *,
    outcome: str,
    now: datetime,
    base_seconds: float = 300.0,
    cap_seconds: float = 3600.0,
) -> tuple[ChainState, str]:
    """Record one tick's outcome and decide `"continue"` vs. `"stop"` for the chain.

    `"progress"` resets the backoff schedule and clears the flight flag, but
    signals `"continue"` -- the caller (the shell wrapper) may immediately
    `begin()` another tick, subject to its own bounded tick-count/wall-clock
    ceilings. Every other outcome (`"idle"`, `"blocked"`, `"paused"`,
    `"unknown"`) escalates `consecutive_non_progress`, schedules
    `next_earliest_attempt` via `compute_backoff_seconds`, and signals
    `"stop"`. Idle and blocked share one schedule deliberately -- both are
    "no new information yet, don't hammer this" -- but each still stays
    distinguishable afterward via `last_outcome`, so health reporting never
    conflates a quiet backlog with a real failure.
    """

    if outcome not in KNOWN_OUTCOMES:
        raise ValueError(f"unknown outcome {outcome!r}; expected one of {sorted(KNOWN_OUTCOMES)}")

    if outcome in CONTINUE_OUTCOMES:
        new_state = replace(
            state,
            in_progress=False,
            pid=None,
            started_at=None,
            consecutive_non_progress=0,
            next_earliest_attempt=None,
            last_outcome=outcome,
            last_updated=format_timestamp(now),
        )
        return new_state, "continue"

    consecutive = state.consecutive_non_progress + 1
    backoff_seconds = compute_backoff_seconds(
        consecutive_non_progress=consecutive, base_seconds=base_seconds, cap_seconds=cap_seconds
    )
    next_earliest_attempt = format_timestamp(now + timedelta(seconds=backoff_seconds))
    new_state = replace(
        state,
        in_progress=False,
        pid=None,
        started_at=None,
        consecutive_non_progress=consecutive,
        next_earliest_attempt=next_earliest_attempt,
        last_outcome=outcome,
        last_updated=format_timestamp(now),
    )
    return new_state, "stop"


def _scalar(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


def _emit_shell(fields: dict[str, object]) -> None:
    for name, value in fields.items():
        sys.stdout.write(f"{name}={shlex.quote(_scalar(value))}\n")


def cmd_gate(args: argparse.Namespace) -> int:
    now = parse_timestamp(args.now) if args.now else datetime.now(timezone.utc)
    state = load_state(args.state_file)
    state, recovered = recover_if_stale(
        state,
        now=now,
        max_age_seconds=args.max_stale_age_seconds,
        base_seconds=args.backoff_base_seconds,
        cap_seconds=args.backoff_cap_seconds,
    )
    if recovered:
        save_state(args.state_file, state)
    allowed, reason = gate(state, now=now)
    _emit_shell(
        {
            "ALLOWED": allowed,
            "REASON": reason,
            "RECOVERED": recovered,
            "CONSECUTIVE_NON_PROGRESS": state.consecutive_non_progress,
            "LAST_OUTCOME": state.last_outcome,
            "NEXT_EARLIEST_ATTEMPT": state.next_earliest_attempt,
        }
    )
    return 0 if allowed else 1


def cmd_begin(args: argparse.Namespace) -> int:
    now = parse_timestamp(args.now) if args.now else datetime.now(timezone.utc)
    state = load_state(args.state_file)
    state = begin(state, pid=args.pid, now=now)
    save_state(args.state_file, state)
    _emit_shell({"IN_PROGRESS": state.in_progress, "STARTED_AT": state.started_at})
    return 0


def cmd_record_outcome(args: argparse.Namespace) -> int:
    now = parse_timestamp(args.now) if args.now else datetime.now(timezone.utc)
    state = load_state(args.state_file)
    try:
        state, action = record_outcome(
            state,
            outcome=args.outcome,
            now=now,
            base_seconds=args.backoff_base_seconds,
            cap_seconds=args.backoff_cap_seconds,
        )
    except ValueError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    save_state(args.state_file, state)
    _emit_shell(
        {
            "ACTION": action,
            "LAST_OUTCOME": state.last_outcome,
            "CONSECUTIVE_NON_PROGRESS": state.consecutive_non_progress,
            "NEXT_EARLIEST_ATTEMPT": state.next_earliest_attempt,
        }
    )
    return 0


def cmd_show(args: argparse.Namespace) -> int:
    state = load_state(args.state_file)
    if args.format == "shell":
        _emit_shell(
            {
                "IN_PROGRESS": state.in_progress,
                "CONSECUTIVE_NON_PROGRESS": state.consecutive_non_progress,
                "NEXT_EARLIEST_ATTEMPT": state.next_earliest_attempt,
                "LAST_OUTCOME": state.last_outcome,
                "LAST_UPDATED": state.last_updated,
            }
        )
    else:
        print(json.dumps(state.to_json_dict(), indent=2))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    subparsers = parser.add_subparsers(dest="command", required=True)

    def add_common(sub: argparse.ArgumentParser) -> None:
        sub.add_argument("--state-file", type=Path, required=True, help="Path to the chain-state JSON file")
        sub.add_argument(
            "--now", default=None, help="Override the current time (UTC, %Y-%m-%dT%H:%M:%SZ); testing only"
        )

    gate_parser = subparsers.add_parser("gate", help="May a new tick start right now?")
    add_common(gate_parser)
    gate_parser.add_argument(
        "--max-stale-age-seconds",
        type=float,
        required=True,
        help="An in_progress flag older than this (or naming a dead pid) is recovered as a crash",
    )
    gate_parser.add_argument("--backoff-base-seconds", type=float, default=300.0)
    gate_parser.add_argument("--backoff-cap-seconds", type=float, default=3600.0)
    gate_parser.set_defaults(func=cmd_gate)

    begin_parser = subparsers.add_parser("begin", help="Mark a tick as in flight")
    add_common(begin_parser)
    begin_parser.add_argument("--pid", type=int, required=True)
    begin_parser.set_defaults(func=cmd_begin)

    record_parser = subparsers.add_parser("record-outcome", help="Record a finished tick's outcome")
    add_common(record_parser)
    record_parser.add_argument("--outcome", required=True, choices=sorted(KNOWN_OUTCOMES))
    record_parser.add_argument("--backoff-base-seconds", type=float, default=300.0)
    record_parser.add_argument("--backoff-cap-seconds", type=float, default=3600.0)
    record_parser.set_defaults(func=cmd_record_outcome)

    show_parser = subparsers.add_parser("show", help="Print the current state without changing it")
    show_parser.add_argument("--state-file", type=Path, required=True)
    show_parser.add_argument("--format", choices=("json", "shell"), default="json")
    show_parser.set_defaults(func=cmd_show)

    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
