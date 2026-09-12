"""Tests for scripts/collect_vps_status.py: the VPS-side collector for the `vps_loop`
operations-status component."""

from __future__ import annotations

import importlib.util
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "collect_vps_status.py"
SPEC = importlib.util.spec_from_file_location("collect_vps_status", SCRIPT)
assert SPEC and SPEC.loader
collector = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = collector
SPEC.loader.exec_module(collector)

T0 = datetime(2026, 9, 11, 12, 0, 0, tzinfo=timezone.utc)


@dataclass
class FakeCompletedProcess:
    returncode: int
    stdout: str = ""
    stderr: str = ""


def runner_from(responses: dict[str, FakeCompletedProcess]):
    """Build a fake `Runner` keyed by ` `-joined argv, raising KeyError (surfaced as a
    test failure) if an unexpected command is issued."""

    def runner(cmd):
        key = " ".join(cmd)
        for prefix, response in responses.items():
            if key.startswith(prefix):
                return response
        raise AssertionError(f"unexpected command: {cmd!r}")

    return runner


def failing_runner(exc: Exception):
    def runner(cmd):
        raise exc

    return runner


# --- classify_loop_activity ---------------------------------------------------


def test_loop_activity_active_when_claude_process_running():
    assert (
        collector.classify_loop_activity(claude_process_state="running", repeated_without_progress=True, paused=True)
        == "active"
    )


def test_loop_activity_restarting_when_repeated_without_progress_and_no_process():
    assert (
        collector.classify_loop_activity(claude_process_state="absent", repeated_without_progress=True, paused=False)
        == "restarting"
    )


def test_loop_activity_restarting_takes_priority_over_paused():
    assert (
        collector.classify_loop_activity(claude_process_state="absent", repeated_without_progress=True, paused=True)
        == "restarting"
    )


def test_loop_activity_paused_when_no_process_and_not_repeated():
    assert (
        collector.classify_loop_activity(claude_process_state="absent", repeated_without_progress=False, paused=True)
        == "paused"
    )


def test_loop_activity_unknown_when_process_state_unknown_and_otherwise_quiet():
    assert (
        collector.classify_loop_activity(claude_process_state="unknown", repeated_without_progress=False, paused=False)
        == "unknown"
    )


def test_loop_activity_idle_when_absent_and_otherwise_quiet():
    assert (
        collector.classify_loop_activity(claude_process_state="absent", repeated_without_progress=False, paused=False)
        == "idle"
    )


# --- query_systemd_unit --------------------------------------------------------


def test_query_systemd_unit_parses_active_healthy_unit():
    runner = runner_from(
        {
            "systemctl show": FakeCompletedProcess(
                0, stdout="ActiveState=inactive\nSubState=dead\nResult=success\nLoadState=loaded\n"
            )
        }
    )
    active_state, sub_state, reported_failure = collector.query_systemd_unit(runner=runner)

    assert active_state == "inactive"
    assert sub_state == "dead"
    assert reported_failure is False


def test_query_systemd_unit_reports_explicit_failure():
    runner = runner_from(
        {
            "systemctl show": FakeCompletedProcess(
                0, stdout="ActiveState=failed\nSubState=failed\nResult=exit-code\nLoadState=loaded\n"
            )
        }
    )
    active_state, _sub_state, reported_failure = collector.query_systemd_unit(runner=runner)

    assert active_state == "failed"
    assert reported_failure is True


def test_query_systemd_unit_reports_failure_from_result_even_if_active_state_differs():
    runner = runner_from(
        {
            "systemctl show": FakeCompletedProcess(
                0, stdout="ActiveState=inactive\nSubState=dead\nResult=failed\nLoadState=loaded\n"
            )
        }
    )
    _active_state, _sub_state, reported_failure = collector.query_systemd_unit(runner=runner)

    assert reported_failure is True


def test_query_systemd_unit_degrades_to_unknown_on_nonzero_exit():
    runner = runner_from({"systemctl show": FakeCompletedProcess(4, stderr="Unit not found.")})
    active_state, sub_state, reported_failure = collector.query_systemd_unit(runner=runner)

    assert (active_state, sub_state, reported_failure) == (None, None, False)


def test_query_systemd_unit_degrades_to_unknown_for_never_installed_unit():
    # `systemctl show` exits 0 even for a unit that was never installed, reporting
    # ActiveState=inactive/SubState=dead -- only LoadState distinguishes this from a
    # legitimately idle, actually-installed unit.
    runner = runner_from(
        {
            "systemctl show": FakeCompletedProcess(
                0, stdout="ActiveState=inactive\nSubState=dead\nResult=success\nLoadState=not-found\n"
            )
        }
    )
    active_state, sub_state, reported_failure = collector.query_systemd_unit(runner=runner)

    assert (active_state, sub_state, reported_failure) == (None, None, False)


def test_query_systemd_unit_degrades_to_unknown_when_systemctl_missing():
    runner = failing_runner(FileNotFoundError("systemctl not found"))
    active_state, sub_state, reported_failure = collector.query_systemd_unit(runner=runner)

    assert (active_state, sub_state, reported_failure) == (None, None, False)


def test_query_systemd_unit_degrades_to_unknown_on_timeout():
    runner = failing_runner(subprocess.TimeoutExpired(cmd="systemctl", timeout=10))
    active_state, sub_state, reported_failure = collector.query_systemd_unit(runner=runner)

    assert (active_state, sub_state, reported_failure) == (None, None, False)


# --- check_claude_process -------------------------------------------------------


def test_check_claude_process_running_on_exit_zero():
    runner = runner_from({"pgrep -f": FakeCompletedProcess(0, stdout="12345\n")})
    assert collector.check_claude_process(runner=runner) == "running"


def test_check_claude_process_absent_on_exit_one():
    runner = runner_from({"pgrep -f": FakeCompletedProcess(1)})
    assert collector.check_claude_process(runner=runner) == "absent"


def test_check_claude_process_unknown_on_unexpected_exit_code():
    runner = runner_from({"pgrep -f": FakeCompletedProcess(2, stderr="pgrep: error")})
    assert collector.check_claude_process(runner=runner) == "unknown"


def test_check_claude_process_unknown_when_pgrep_missing():
    runner = failing_runner(FileNotFoundError("pgrep not found"))
    assert collector.check_claude_process(runner=runner) == "unknown"


# --- gather_git_facts ------------------------------------------------------------


def test_gather_git_facts_parses_branch_worktrees_and_stashes():
    runner = runner_from(
        {
            "git -C /repo rev-parse": FakeCompletedProcess(0, stdout="vps-loop/item-120\n"),
            "git -C /repo worktree list": FakeCompletedProcess(
                0,
                stdout=(
                    "worktree /root/transit-app\nHEAD abc\nbranch refs/heads/main\n\n"
                    "worktree /root/transit-app/.claude/worktrees/w1\nHEAD def\nbranch refs/heads/w1\n"
                ),
            ),
            "git -C /repo stash list": FakeCompletedProcess(0, stdout="stash@{0}: WIP on main: abc\n"),
        }
    )
    branch, worktree_count, stash_count = collector.gather_git_facts(Path("/repo"), runner=runner)

    assert branch == "vps-loop/item-120"
    assert worktree_count == 2
    assert stash_count == 1


ONE_WORKTREE_PORCELAIN = "worktree /repo\nHEAD abc\nbranch refs/heads/main\n"


def test_gather_git_facts_empty_stash_list_counts_zero():
    runner = runner_from(
        {
            "git -C /repo rev-parse": FakeCompletedProcess(0, stdout="main\n"),
            "git -C /repo worktree list": FakeCompletedProcess(0, stdout=ONE_WORKTREE_PORCELAIN),
            "git -C /repo stash list": FakeCompletedProcess(0, stdout=""),
        }
    )
    _branch, _worktree_count, stash_count = collector.gather_git_facts(Path("/repo"), runner=runner)

    assert stash_count == 0


def test_gather_git_facts_degrades_each_field_independently_on_failure():
    runner = runner_from(
        {
            "git -C /repo rev-parse": FakeCompletedProcess(128, stderr="not a git repository"),
            "git -C /repo worktree list": FakeCompletedProcess(0, stdout=ONE_WORKTREE_PORCELAIN),
            "git -C /repo stash list": FakeCompletedProcess(128, stderr="not a git repository"),
        }
    )
    branch, worktree_count, stash_count = collector.gather_git_facts(Path("/repo"), runner=runner)

    assert branch is None
    assert worktree_count == 1
    assert stash_count is None


def test_gather_git_facts_degrades_to_all_none_when_git_missing():
    runner = failing_runner(FileNotFoundError("git not found"))
    branch, worktree_count, stash_count = collector.gather_git_facts(Path("/repo"), runner=runner)

    assert (branch, worktree_count, stash_count) == (None, None, None)


# --- gather_disk_usage ------------------------------------------------------------


@dataclass
class FakeDiskUsage:
    total: int
    used: int
    free: int


def test_gather_disk_usage_computes_pct_and_free_bytes():
    used_pct, free_bytes = collector.gather_disk_usage(
        Path("/repo"), usage_fn=lambda _path: FakeDiskUsage(total=100, used=75, free=25)
    )

    assert used_pct == 75.0
    assert free_bytes == 25


def test_gather_disk_usage_none_on_oserror():
    def raiser(_path):
        raise OSError("no such path")

    assert collector.gather_disk_usage(Path("/repo"), usage_fn=raiser) == (None, None)


def test_gather_disk_usage_none_when_total_is_zero():
    used_pct, free_bytes = collector.gather_disk_usage(
        Path("/repo"), usage_fn=lambda _path: FakeDiskUsage(total=0, used=0, free=0)
    )

    assert (used_pct, free_bytes) == (None, None)


# --- build_vps_loop_status: ComponentStatus state transitions ---------------------


def make_health_report(
    *,
    last_successful_tick: str | None = "2026-09-11T11:00:00Z",
    current_item=120,
    blocker_class=None,
    paused=False,
    repeated_without_progress=False,
    stale_pause=False,
    tick_interval_seconds=3600,
) -> dict:
    return {
        "last_successful_tick": last_successful_tick,
        "current_item": current_item,
        "blocker_class": blocker_class,
        "paused": paused,
        "paused_since": None,
        "tick_interval_seconds": tick_interval_seconds,
        "tick_interval_source": "parsed",
        "reduced_probe_cadence_seconds": tick_interval_seconds * 3.0,
        "recent_blocker_tags": [],
        "alerts": {"repeated_without_progress": repeated_without_progress, "stale_pause": stale_pause},
    }


def make_facts(*, health_report: dict, now: datetime = T0, **overrides) -> "collector.VpsFacts":
    defaults = dict(
        now=now,
        health_report=health_report,
        systemd_active_state="inactive",
        systemd_sub_state="dead",
        systemd_reported_failure=False,
        claude_process_state="absent",
        branch="main",
        worktree_count=2,
        stash_count=0,
        disk_used_pct=42.0,
        disk_free_bytes=123456,
        chain_state=collector.vps_loop_chain_state.ChainState(),
    )
    defaults.update(overrides)
    return collector.VpsFacts(**defaults)


def test_build_status_healthy_when_recent_success():
    facts = make_facts(health_report=make_health_report(last_successful_tick="2026-09-11T11:30:00Z"))  # 30 min ago
    status = collector.build_vps_loop_status(facts)

    assert status.state == "healthy"
    assert status.details["loop_activity"] == "idle"


def test_build_status_degraded_past_healthy_threshold():
    # healthy_max = 3600 * 1.5 = 5400s; degraded window up to stale_max = 3600*3 = 10800s
    facts = make_facts(health_report=make_health_report(last_successful_tick="2026-09-11T10:00:00Z"))  # 2h ago = 7200s
    status = collector.build_vps_loop_status(facts)

    assert status.state == "degraded"


def test_build_status_stale_past_stale_threshold():
    facts = make_facts(health_report=make_health_report(last_successful_tick="2026-09-11T06:00:00Z"))  # 6h ago
    status = collector.build_vps_loop_status(facts)

    assert status.state == "stale"


def test_build_status_failed_when_systemd_reports_failure_regardless_of_age():
    # 1 minute ago, otherwise healthy -- reported_failure must override that.
    facts = make_facts(
        health_report=make_health_report(last_successful_tick="2026-09-11T11:59:00Z"),
        systemd_reported_failure=True,
    )
    status = collector.build_vps_loop_status(facts)

    assert status.state == "failed"


def test_build_status_unknown_when_no_success_ever_observed():
    facts = make_facts(health_report=make_health_report(last_successful_tick=None))
    status = collector.build_vps_loop_status(facts)

    assert status.state == "unknown"
    assert status.age_seconds is None


def test_build_status_details_reflect_restarting_activity_and_blocker():
    facts = make_facts(
        health_report=make_health_report(
            last_successful_tick="2026-09-11T11:30:00Z",
            blocker_class="db-write-blocked",
            repeated_without_progress=True,
        ),
        claude_process_state="absent",
    )
    status = collector.build_vps_loop_status(facts)

    assert status.details["loop_activity"] == "restarting"
    assert status.details["blocker_class"] == "db-write-blocked"
    assert status.details["repeated_without_progress"] is True


def test_build_status_details_reflect_active_process():
    facts = make_facts(
        health_report=make_health_report(last_successful_tick="2026-09-11T11:30:00Z"),
        claude_process_state="running",
    )
    status = collector.build_vps_loop_status(facts)

    assert status.details["loop_activity"] == "active"
    assert status.details["claude_process_state"] == "running"


def test_build_status_details_reflect_chain_state_backoff():
    facts = make_facts(
        health_report=make_health_report(last_successful_tick="2026-09-11T11:30:00Z"),
        chain_state=collector.vps_loop_chain_state.ChainState(
            consecutive_non_progress=2,
            next_earliest_attempt="2026-09-11T13:00:00Z",
            last_outcome="idle",
        ),
    )
    status = collector.build_vps_loop_status(facts)

    assert status.details["chain_in_progress"] is False
    assert status.details["chain_consecutive_non_progress"] == 2
    assert status.details["chain_next_earliest_attempt"] == "2026-09-11T13:00:00Z"
    assert status.details["chain_last_outcome"] == "idle"


def test_build_status_document_is_contract_valid():
    facts = make_facts(health_report=make_health_report(last_successful_tick="2026-09-11T11:30:00Z"))
    status = collector.build_vps_loop_status(facts)

    # build_status already validates internally; re-validating here pins the
    # collector's own output to the shared contract as a regression guard.
    collector.ops_status.validate_component_status(status)
    document = collector.ops_status.to_json_dict(status)
    assert document["component"] == "vps_loop"


# --- collect_vps_facts / collect_vps_loop_status (end to end) --------------------


def write_next_task(tmp_path: Path, status_log: str) -> tuple[Path, Path]:
    next_task = tmp_path / "NEXT_TASK.md"
    next_task.write_text(
        "# Refactor backlog\n\n1. **DONE (PR #1, merged 2026-01-01) — First item.**\n2. **Second item.**\n\n"
        "# Status log\n\n" + status_log,
        encoding="utf-8",
    )
    timer = tmp_path / "claude-loop.timer"
    timer.write_text("[Timer]\nOnCalendar=*-*-* *:03:00\n", encoding="utf-8")
    return next_task, timer


def test_collect_vps_facts_raises_when_next_task_missing(tmp_path):
    with pytest.raises(collector.VpsStatusUnavailable):
        collector.collect_vps_facts(repo=tmp_path, next_task_path=tmp_path / "NEXT_TASK.md")


def test_collect_vps_facts_end_to_end_with_injected_runners(tmp_path):
    next_task, timer = write_next_task(tmp_path, "- 2026-09-11T11:30:00Z: item 120 shipped as PR #1.\n")

    systemd_runner = runner_from(
        {
            "systemctl show": FakeCompletedProcess(
                0, stdout="ActiveState=inactive\nSubState=dead\nResult=success\nLoadState=loaded\n"
            )
        }
    )
    process_runner = runner_from({"pgrep -f": FakeCompletedProcess(1)})
    git_runner = runner_from(
        {
            f"git -C {tmp_path} rev-parse": FakeCompletedProcess(0, stdout="vps-loop/item-120\n"),
            f"git -C {tmp_path} worktree list": FakeCompletedProcess(0, stdout=ONE_WORKTREE_PORCELAIN),
            f"git -C {tmp_path} stash list": FakeCompletedProcess(0, stdout=""),
        }
    )

    facts = collector.collect_vps_facts(
        repo=tmp_path,
        next_task_path=next_task,
        timer_path=timer,
        chain_state_path=tmp_path / "chain-state.json",
        now=T0,
        systemd_runner=systemd_runner,
        process_runner=process_runner,
        git_runner=git_runner,
        disk_usage_fn=lambda _path: FakeDiskUsage(total=100, used=10, free=90),
    )

    assert facts.health_report["current_item"] == 120
    assert facts.systemd_active_state == "inactive"
    assert facts.claude_process_state == "absent"
    assert facts.branch == "vps-loop/item-120"
    # A never-yet-written chain-state file (fresh provision, or this test's
    # own tmp_path) degrades to a fresh default rather than erroring.
    assert facts.chain_state == collector.vps_loop_chain_state.ChainState()
    assert facts.worktree_count == 1
    assert facts.stash_count == 0
    assert facts.disk_used_pct == 10.0

    status = collector.build_vps_loop_status(facts)
    assert status.component == "vps_loop"
    assert status.state == "healthy"


# --- CLI -----------------------------------------------------------------------


def test_main_exit_code_2_when_next_task_missing(tmp_path, capsys):
    exit_code = collector.main(["--repo", str(tmp_path)])

    assert exit_code == 2
    assert "does not exist" in capsys.readouterr().err


def test_main_exit_code_0_for_a_freshly_shipped_tick(tmp_path, capsys):
    # main() has no `now` override, so the fixture's timestamp is derived from
    # the real wall clock (a few minutes ago) rather than a fixed literal, to
    # stay healthy regardless of when this test happens to run.
    recent = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    next_task, timer = write_next_task(tmp_path, f"- {recent}: item 120 shipped as PR #1.\n")

    exit_code = collector.main(
        [
            "--repo",
            str(tmp_path),
            "--file",
            str(next_task),
            "--timer-file",
            str(timer),
            "--chain-state-file",
            str(tmp_path / "chain-state.json"),
        ]
    )

    payload = capsys.readouterr().out
    assert exit_code == 0
    assert '"component": "vps_loop"' in payload
    assert '"schema_version": 1' in payload


def test_main_exit_code_1_when_never_observed_a_success(tmp_path, capsys):
    next_task, timer = write_next_task(tmp_path, "")

    exit_code = collector.main(
        [
            "--repo",
            str(tmp_path),
            "--file",
            str(next_task),
            "--timer-file",
            str(timer),
            "--chain-state-file",
            str(tmp_path / "chain-state.json"),
        ]
    )

    payload = capsys.readouterr().out
    assert exit_code == 1
    assert '"state": "unknown"' in payload
