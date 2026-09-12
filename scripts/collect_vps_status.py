#!/usr/bin/env python3
"""Collect the VPS/Claude-loop's own operations-status snapshot (component `vps_loop`).

`scripts/vps_loop_health.py` already parses `NEXT_TASK.md`'s Status log into
`last_successful_tick`, `current_item`, `blocker_class`, pause state, and the
`repeated_without_progress`/`stale_pause` alert flags -- this module reuses that
report rather than re-deriving it, and adds the facts only the VPS host itself
can observe: the `claude-loop.service` systemd unit's own reported state, whether
a `claude` CLI process is currently running, the checkout's branch/worktree/stash
counts, and local disk usage. Together these are folded into one
`ops_status.ComponentStatus` for `component="vps_loop"`.

`claude-loop.service` is a `Type=oneshot` unit invoked hourly by `claude-loop.timer`
(see `deploy/vps/claude-loop.sh`): a `claude` process is only alive for the
duration of one tick, so its absence at any given moment is the overwhelmingly
common case, not evidence of a problem. `check_claude_process` therefore reports
a tri-state `"running"`/`"absent"`/`"unknown"` fact rather than a plain boolean --
"unknown" (pgrep itself unavailable or failed unexpectedly) is kept distinct from
a confirmed "absent" so a collection-tooling gap is never silently read as "the
loop is idle." The same "explain, don't assume failure" rule applies to
`query_systemd_unit`: only an explicit `ActiveState=failed`/`Result=failed` from
systemd feeds `reported_failure` into the contract's `classify_state` (which
forces `state="failed"` regardless of age) -- a systemd query that itself fails
(unit not found, no systemd in this environment) degrades to an `"unknown"`
detail value, never to a fabricated failure.

`classify_loop_activity` distinguishes active work from a repeatedly restarting
or idle loop using a fixed priority order, most-direct evidence first: a
currently-running `claude` process is the strongest signal and wins outright
("active"); otherwise three ticks in a row stuck on the same blocker tag
(`repeated_without_progress`, already computed by `vps_loop_health`) means
"restarting" even though nothing is running right now; otherwise the loop's own
circuit-breaker pause state means "paused"; otherwise an `"unknown"` process
read means the loop's activity itself can't be determined; only once none of
those apply is it "idle" -- the ordinary, expected state between ticks.

`scripts/vps_loop_chain_state.py`'s own persisted bookkeeping (is a tick
currently in flight, the consecutive-non-progress count, and the scheduled
backoff, if any) is folded in as-is under `chain_*` `details` keys -- this
module never recomputes or second-guesses that state, only surfaces it.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import shutil
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Sequence

_SCRIPT_DIR = Path(__file__).resolve().parent


def _load_sibling(name: str):
    """Load another `scripts/*.py` module by file path.

    `scripts/` has no `__init__.py` and is not guaranteed to be importable as a
    package from wherever this script is actually invoked (see
    `vps_loop_health.py`'s identical rationale for loading `reconcile_next_task.py`
    the same way), so this avoids depending on `sys.path` layout.
    """

    spec = importlib.util.spec_from_file_location(name, _SCRIPT_DIR / f"{name}.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


ops_status = _load_sibling("ops_status")
vps_loop_health = _load_sibling("vps_loop_health")
vps_loop_chain_state = _load_sibling("vps_loop_chain_state")


DEFAULT_SERVICE_UNIT = "claude-loop.service"
# Not repo-relative like `next_task_path`'s default: this is `deploy/vps/
# claude-loop.sh`'s own wrapper-level bookkeeping (see
# `scripts/vps_loop_chain_state.py`), not tracked repo content, so it lives
# alongside that wrapper's other VPS-local state under `/root/`.
DEFAULT_CHAIN_STATE_FILE = Path("/root/vps-loop-chain-state.json")
# Healthy up to 1.5 tick intervals (scheduling jitter past the top of the hour
# is normal); stale past `vps_loop_health`'s own reduced-probe-cadence
# threshold alone (`tick_interval * probe_multiplier`, default 3.0) -- 1.5x
# earlier than that module's own `stale_pause` alert, which additionally
# applies its `stale_pause_buffer` on top before treating a paused loop as
# overdue for a fresh bookkeeping entry.
DEFAULT_HEALTHY_MULTIPLIER = 1.5
DEFAULT_STALE_MULTIPLIER = 3.0
# Matches `deploy/vps/claude-loop.sh`'s own invocation of the CLI.
CLAUDE_PROCESS_PATTERN = "claude --model"

LOOP_ACTIVITY_STATES = frozenset({"active", "restarting", "paused", "idle", "unknown"})

Runner = Callable[[Sequence[str]], "subprocess.CompletedProcess[str]"]


class VpsStatusUnavailable(Exception):
    """Raised when `NEXT_TASK.md` itself cannot be read.

    Every other input (systemd, process check, git, disk) degrades its own
    field to `None`/`"unknown"` instead of raising -- only the Status log is
    load-bearing enough that its absence should abort collection outright.
    """


@dataclass(frozen=True)
class VpsFacts:
    """Every raw fact `build_vps_loop_status` needs, gathered once by `collect_vps_facts`.

    Kept separate from the gathering step so state-transition tests can
    construct this directly instead of mocking subprocess/filesystem calls.
    """

    now: datetime
    health_report: dict
    systemd_active_state: str | None
    systemd_sub_state: str | None
    systemd_reported_failure: bool
    claude_process_state: str  # "running" | "absent" | "unknown"
    branch: str | None
    worktree_count: int | None
    stash_count: int | None
    disk_used_pct: float | None
    disk_free_bytes: int | None
    # Typed loosely (like `health_report` above) rather than as
    # `vps_loop_chain_state.ChainState`: that module is loaded dynamically
    # via `_load_sibling`, so mypy has no static definition for its name to
    # resolve a forward reference against.
    chain_state: Any


def classify_loop_activity(*, claude_process_state: str, repeated_without_progress: bool, paused: bool) -> str:
    """Derive one of `LOOP_ACTIVITY_STATES` from the raw facts. See the module docstring
    for the priority order and its rationale."""

    if claude_process_state == "running":
        result = "active"
    elif repeated_without_progress:
        result = "restarting"
    elif paused:
        result = "paused"
    elif claude_process_state == "unknown":
        result = "unknown"
    else:
        result = "idle"
    assert result in LOOP_ACTIVITY_STATES
    return result


def _run(cmd: Sequence[str], *, timeout: float = 10.0) -> "subprocess.CompletedProcess[str]":
    return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, check=False)


def query_systemd_unit(
    unit: str = DEFAULT_SERVICE_UNIT,
    *,
    runner: Runner = _run,
) -> tuple[str | None, str | None, bool]:
    """Return `(active_state, sub_state, reported_failure)` for one systemd unit.

    `reported_failure` is only `True` on an explicit `ActiveState=failed` or
    `Result=failed` from systemd itself -- any other outcome (unit not found,
    `systemctl` unavailable, a timeout) degrades to `(None, None, False)`
    rather than guessing at failure from the absence of a clean read.
    """

    try:
        proc = runner(["systemctl", "show", unit, "--property=ActiveState,SubState,Result,LoadState"])
    except (OSError, subprocess.TimeoutExpired):
        return None, None, False
    if proc.returncode != 0:
        return None, None, False

    fields: dict[str, str] = {}
    for line in proc.stdout.splitlines():
        key, sep, value = line.partition("=")
        if sep:
            fields[key] = value

    if fields.get("LoadState") != "loaded":
        # `systemctl show` exits 0 even for a unit that was never installed,
        # reporting ActiveState=inactive/SubState=dead -- indistinguishable
        # from a legitimately idle unit unless LoadState is checked too.
        return None, None, False

    active_state = fields.get("ActiveState") or None
    sub_state = fields.get("SubState") or None
    result = fields.get("Result") or None
    reported_failure = active_state == "failed" or result == "failed"
    return active_state, sub_state, reported_failure


def check_claude_process(*, runner: Runner = _run) -> str:
    """`"running"` / `"absent"` / `"unknown"` -- see the module docstring for why this is
    tri-state rather than a plain boolean."""

    try:
        proc = runner(["pgrep", "-f", CLAUDE_PROCESS_PATTERN])
    except (OSError, subprocess.TimeoutExpired):
        return "unknown"
    if proc.returncode == 0:
        return "running"
    if proc.returncode == 1:
        return "absent"
    return "unknown"


def gather_git_facts(repo: Path, *, runner: Runner = _run) -> tuple[str | None, int | None, int | None]:
    """Return `(branch, worktree_count, stash_count)`.

    Each of the three git calls degrades independently to `None` on failure
    (not a git repo, `git` missing, a timeout) rather than aborting the other
    two -- one broken fact should never hide the other two.
    """

    branch: str | None = None
    try:
        proc = runner(["git", "-C", str(repo), "rev-parse", "--abbrev-ref", "HEAD"])
        if proc.returncode == 0:
            branch = proc.stdout.strip() or None
    except (OSError, subprocess.TimeoutExpired):
        pass

    worktree_count: int | None = None
    try:
        proc = runner(["git", "-C", str(repo), "worktree", "list", "--porcelain"])
        if proc.returncode == 0:
            worktree_count = sum(1 for line in proc.stdout.splitlines() if line.startswith("worktree "))
    except (OSError, subprocess.TimeoutExpired):
        pass

    stash_count: int | None = None
    try:
        proc = runner(["git", "-C", str(repo), "stash", "list"])
        if proc.returncode == 0:
            stash_count = sum(1 for line in proc.stdout.splitlines() if line.strip())
    except (OSError, subprocess.TimeoutExpired):
        pass

    return branch, worktree_count, stash_count


def gather_disk_usage(
    path: Path,
    *,
    usage_fn: Callable[[str], object] = shutil.disk_usage,
) -> tuple[float | None, int | None]:
    """Return `(used_pct, free_bytes)` for the filesystem containing `path`, or
    `(None, None)` if it cannot be determined."""

    try:
        usage = usage_fn(str(path))
        total = usage.total  # type: ignore[attr-defined]
        used = usage.used  # type: ignore[attr-defined]
        free = usage.free  # type: ignore[attr-defined]
    except (OSError, AttributeError):
        return None, None
    if total <= 0:
        return None, None
    return round(used / total * 100, 1), free


def _parse_health_timestamp(value: str | None) -> datetime | None:
    if value is None:
        return None
    return datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)


def build_vps_loop_status(
    facts: VpsFacts,
    *,
    healthy_multiplier: float = DEFAULT_HEALTHY_MULTIPLIER,
    stale_multiplier: float = DEFAULT_STALE_MULTIPLIER,
):
    """Build and validate the `vps_loop` component's `ComponentStatus` from already-gathered `facts`."""

    health = facts.health_report
    alerts = health["alerts"]
    tick_interval_seconds = health["tick_interval_seconds"]
    last_success_at = _parse_health_timestamp(health["last_successful_tick"])

    activity = classify_loop_activity(
        claude_process_state=facts.claude_process_state,
        repeated_without_progress=alerts["repeated_without_progress"],
        paused=health["paused"],
    )

    chain_state = facts.chain_state
    details = {
        "loop_activity": activity,
        "claude_process_state": facts.claude_process_state,
        "systemd_active_state": facts.systemd_active_state or "unknown",
        "systemd_sub_state": facts.systemd_sub_state or "unknown",
        "current_item": health["current_item"],
        "last_tick_outcome": health.get("last_tick_outcome"),
        "blocker_class": health["blocker_class"],
        "paused": health["paused"],
        "repeated_without_progress": alerts["repeated_without_progress"],
        "stale_pause": alerts["stale_pause"],
        "branch": facts.branch or "unknown",
        "worktree_count": facts.worktree_count,
        "stash_count": facts.stash_count,
        "disk_used_pct": facts.disk_used_pct,
        "disk_free_bytes": facts.disk_free_bytes,
        # Guarded-continuation (chained-tick) bookkeeping -- see
        # scripts/vps_loop_chain_state.py. `chain_in_progress` staying `True`
        # here for longer than one tick's own timeout is itself a sign of a
        # wedged wrapper process (the next invocation's own `gate` call would
        # recover it, but this status snapshot can surface it sooner).
        "chain_in_progress": chain_state.in_progress,
        "chain_consecutive_non_progress": chain_state.consecutive_non_progress,
        "chain_next_earliest_attempt": chain_state.next_earliest_attempt,
        "chain_last_outcome": chain_state.last_outcome,
    }

    return ops_status.build_status(
        component="vps_loop",
        observed_at=facts.now,
        last_success_at=last_success_at,
        healthy_max_age_seconds=tick_interval_seconds * healthy_multiplier,
        stale_max_age_seconds=tick_interval_seconds * stale_multiplier,
        reported_failure=facts.systemd_reported_failure,
        details=details,
        now=facts.now,
    )


def collect_vps_facts(
    *,
    repo: Path,
    next_task_path: Path | None = None,
    timer_path: Path | None = None,
    chain_state_path: Path | None = None,
    service_unit: str = DEFAULT_SERVICE_UNIT,
    tick_interval_seconds: int | None = None,
    probe_multiplier: float = 3.0,
    stale_pause_buffer: float = 1.5,
    now: datetime | None = None,
    systemd_runner: Runner = _run,
    process_runner: Runner = _run,
    git_runner: Runner = _run,
    disk_usage_fn: Callable[[str], object] = shutil.disk_usage,
) -> VpsFacts:
    """Gather every raw fact for `repo`'s VPS loop, doing the actual systemd/process/git/disk I/O."""

    now = now or datetime.now(timezone.utc)
    next_task_path = next_task_path or (repo / "NEXT_TASK.md")
    timer_path = timer_path or (repo / "deploy" / "systemd" / "claude-loop.timer")
    chain_state_path = chain_state_path or DEFAULT_CHAIN_STATE_FILE

    if not next_task_path.exists():
        raise VpsStatusUnavailable(f"{next_task_path} does not exist")

    health_report = vps_loop_health.build_report(
        next_task_path=next_task_path,
        timer_path=timer_path,
        tick_interval_seconds=tick_interval_seconds,
        probe_multiplier=probe_multiplier,
        stale_pause_buffer=stale_pause_buffer,
        now=now,
    )

    active_state, sub_state, reported_failure = query_systemd_unit(service_unit, runner=systemd_runner)
    claude_process_state = check_claude_process(runner=process_runner)
    branch, worktree_count, stash_count = gather_git_facts(repo, runner=git_runner)
    disk_used_pct, disk_free_bytes = gather_disk_usage(repo, usage_fn=disk_usage_fn)
    # A missing/corrupt chain-state file (first run ever, or a fresh VPS
    # provision) degrades to a fresh default state, same as
    # `vps_loop_chain_state.load_state` itself does for its own callers --
    # never a reason to abort the whole status collection.
    chain_state = vps_loop_chain_state.load_state(chain_state_path)

    return VpsFacts(
        now=now,
        health_report=health_report,
        systemd_active_state=active_state,
        systemd_sub_state=sub_state,
        systemd_reported_failure=reported_failure,
        claude_process_state=claude_process_state,
        branch=branch,
        worktree_count=worktree_count,
        stash_count=stash_count,
        disk_used_pct=disk_used_pct,
        disk_free_bytes=disk_free_bytes,
        chain_state=chain_state,
    )


def collect_vps_loop_status(
    *,
    healthy_multiplier: float = DEFAULT_HEALTHY_MULTIPLIER,
    stale_multiplier: float = DEFAULT_STALE_MULTIPLIER,
    **facts_kwargs,
):
    """`collect_vps_facts` + `build_vps_loop_status` in one call."""

    facts = collect_vps_facts(**facts_kwargs)
    return build_vps_loop_status(facts, healthy_multiplier=healthy_multiplier, stale_multiplier=stale_multiplier)


def main(argv: Sequence[str] | None = None) -> int:
    """CLI entry point: prints the `vps_loop` component's status document as JSON.

    Exit code: 0 when `healthy`/`degraded`, 1 when `stale`/`failed`/`unknown`
    (mirrors `vps_loop_health.py`'s own "1 means this needs attention"
    convention), 2 on a hard error (missing `NEXT_TASK.md` or a document that
    fails contract validation).
    """

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
        "--chain-state-file",
        type=Path,
        default=DEFAULT_CHAIN_STATE_FILE,
        help="scripts/vps_loop_chain_state.py state file (default: /root/vps-loop-chain-state.json)",
    )
    parser.add_argument("--service-unit", default=DEFAULT_SERVICE_UNIT, help="systemd service unit to query")
    parser.add_argument("--tick-interval-seconds", type=int, default=None)
    parser.add_argument("--probe-multiplier", type=float, default=3.0)
    parser.add_argument("--stale-pause-buffer", type=float, default=1.5)
    parser.add_argument("--healthy-multiplier", type=float, default=DEFAULT_HEALTHY_MULTIPLIER)
    parser.add_argument("--stale-multiplier", type=float, default=DEFAULT_STALE_MULTIPLIER)
    parser.add_argument("--out", type=Path, default=None, help="Also write the status document as JSON to this path")
    args = parser.parse_args(argv)

    repo = args.repo.resolve()
    next_task_path = (args.file or repo / "NEXT_TASK.md").resolve()
    timer_path = (args.timer_file or repo / "deploy" / "systemd" / "claude-loop.timer").resolve()

    try:
        status = collect_vps_loop_status(
            repo=repo,
            next_task_path=next_task_path,
            timer_path=timer_path,
            chain_state_path=args.chain_state_file.resolve(),
            service_unit=args.service_unit,
            tick_interval_seconds=args.tick_interval_seconds,
            probe_multiplier=args.probe_multiplier,
            stale_pause_buffer=args.stale_pause_buffer,
            healthy_multiplier=args.healthy_multiplier,
            stale_multiplier=args.stale_multiplier,
        )
    except VpsStatusUnavailable as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    except ops_status.OpsStatusError as exc:
        print(f"ERROR: status document failed contract validation: {exc}", file=sys.stderr)
        return 2

    document = ops_status.to_json_dict(status)
    if args.out is not None:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(json.dumps(document, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(document, indent=2))

    return 0 if status.state in ("healthy", "degraded") else 1


if __name__ == "__main__":
    raise SystemExit(main())
