"""Tests for exporting /vps-loop-run health state and stuck/stale-pause alerts."""

from __future__ import annotations

import importlib.util
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "vps_loop_health.py"
SPEC = importlib.util.spec_from_file_location("vps_loop_health", SCRIPT)
assert SPEC and SPEC.loader
health = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = health
SPEC.loader.exec_module(health)


def entry(text: str) -> health.StatusEntry:
    """Parse a single raw entry block (as `split_status_log_entries` would yield it)."""

    return health.parse_entry(text)


# --- split_status_log_entries / parse_entry ---------------------------------


def test_split_status_log_entries_ignores_backlog_and_groups_continuation_lines():
    text = (
        "# Refactor backlog\n"
        "\n"
        "1. **Some item.** Detail text with a trailing dash line:\n"
        "   - not a log entry, indented\n"
        "\n"
        "# Status log\n"
        "\n"
        "- 2026-09-01T00:00:00Z: first entry line one.\n"
        "  continuation line, indented.\n"
        "- 2026-09-01T01:00:00Z: second entry.\n"
    )
    blocks = health.split_status_log_entries(text)

    assert len(blocks) == 2
    assert blocks[0].startswith("- 2026-09-01T00:00:00Z: first entry line one.")
    assert "continuation line" in blocks[0]
    assert blocks[1] == "- 2026-09-01T01:00:00Z: second entry."


def test_split_status_log_entries_empty_without_heading():
    assert health.split_status_log_entries("# Refactor backlog\n\n1. **Item.**\n") == []


def test_parse_entry_extracts_timestamp_blocker_tag_and_item_number():
    parsed = entry(
        "- 2026-09-01T12:05:17Z: item 45 blocked before verification — worker reported: X.\n"
        "  **Blocker-tag:** sensitive-path-no-approver."
    )

    assert parsed.timestamp == "2026-09-01T12:05:17Z"
    assert parsed.kind == "normal"
    assert parsed.blocker_tag == "sensitive-path-no-approver"
    assert parsed.item_number == 45


def test_parse_entry_recognizes_paused_still_paused_and_resumed_markers():
    paused = entry(
        "- 2026-09-01T15:23:47Z: **PAUSED after the last 3 ticks blocked on\n"
        "  sensitive-path-no-approver. Backing off to a reduced probe cadence until\n"
        "  this clears or a human resolves it.**"
    )
    still_paused = entry("- 2026-09-01T16:00:00Z: **Still paused — probe found sensitive-path-no-approver unchanged.**")
    resumed = entry("- 2026-09-01T17:00:00Z: **RESUMED — sensitive-path-no-approver cleared (paused since X).**")

    assert paused.kind == "paused"
    assert still_paused.kind == "still_paused"
    assert resumed.kind == "resumed"
    # Bookkeeping entries carry no Blocker-tag of their own.
    assert still_paused.blocker_tag is None
    assert resumed.blocker_tag is None


def test_parse_entry_blocker_tag_none_is_not_a_real_tag():
    parsed = entry(
        "- 2026-09-01T00:00:00Z: item 56 shipped end-to-end this tick.\n"
        "  **Blocker-tag:** none — this is not a tick-stopping blocker."
    )

    assert parsed.blocker_tag is None


def test_parse_entry_handles_wrapped_timestamp_annotation_without_crashing():
    # One real historical shape: the timestamp itself is annotated and wraps
    # across lines before its closing "): ". Entry boundary detection and
    # blocker-tag/item extraction must still work; only `timestamp` degrades.
    parsed = entry(
        "- 2026-09-10T~03:19Z (approximate — `date` was unavailable once the blocker\n"
        "  below hit; `03:19:34Z` is a hard lower bound, derived from elsewhere):\n"
        "  **RESUMED — agent-dispatch-safety-blocked cleared (paused since\n"
        "  2026-09-09T23:23:54Z).** item 105 resumed."
    )

    assert parsed.timestamp is None
    assert parsed.item_number == 105


# --- compute_pause_state ------------------------------------------------------


def test_compute_pause_state_true_after_paused_with_no_later_resumed():
    entries = [
        entry("- 2026-09-01T00:00:00Z: item 1 blocked. **Blocker-tag:** foo"),
        entry("- 2026-09-01T01:00:00Z: **PAUSED after the last 3 ticks blocked on foo. Backing off.**"),
    ]
    paused, anchor = health.compute_pause_state(entries)

    assert paused is True
    assert anchor is not None
    assert anchor.timestamp == "2026-09-01T01:00:00Z"


def test_compute_pause_state_still_paused_extends_the_pause():
    entries = [
        entry("- 2026-09-01T01:00:00Z: **PAUSED after the last 3 ticks blocked on foo. Backing off.**"),
        entry("- 2026-09-01T02:00:00Z: **Still paused — probe found foo unchanged.**"),
    ]
    paused, anchor = health.compute_pause_state(entries)

    assert paused is True
    assert anchor is not None
    assert anchor.timestamp == "2026-09-01T02:00:00Z"


def test_compute_pause_state_false_after_resumed():
    entries = [
        entry("- 2026-09-01T01:00:00Z: **PAUSED after the last 3 ticks blocked on foo. Backing off.**"),
        entry("- 2026-09-01T02:00:00Z: **RESUMED — foo cleared (paused since X).**"),
    ]
    paused, anchor = health.compute_pause_state(entries)

    assert paused is False
    assert anchor is None


def test_compute_pause_state_false_with_no_bookkeeping_entries_at_all():
    entries = [entry("- 2026-09-01T00:00:00Z: item 1 shipped as PR #1.")]
    paused, anchor = health.compute_pause_state(entries)

    assert paused is False
    assert anchor is None


# --- compute_recent_blocker_tags / compute_repeated_without_progress ---------


def test_recent_blocker_tags_bounded_by_most_recent_resumed():
    entries = [
        entry("- 2026-09-01T00:00:00Z: item 1 blocked. **Blocker-tag:** old-tag"),
        entry("- 2026-09-01T01:00:00Z: **RESUMED — old-tag cleared (paused since X).**"),
        entry("- 2026-09-01T02:00:00Z: item 2 blocked. **Blocker-tag:** new-tag"),
    ]
    tags = health.compute_recent_blocker_tags(entries)

    assert tags == ["new-tag"]


def test_repeated_without_progress_true_only_for_three_identical_tags():
    same = ["x", "x", "x"]
    mixed = ["x", "y", "x"]
    short = ["x", "x"]

    assert health.compute_repeated_without_progress(same) is True
    assert health.compute_repeated_without_progress(mixed) is False
    assert health.compute_repeated_without_progress(short) is False


def test_repeated_without_progress_unrelated_success_does_not_break_the_streak():
    # Mirrors vps-loop-run.md's own worked example: an unrelated shipped tick
    # between two occurrences of the same tag does not reset the count.
    entries = [
        entry("- 2026-09-01T00:00:00Z: item 21 blocked. **Blocker-tag:** db-write-blocked"),
        entry("- 2026-09-01T00:20:00Z: item 22 blocked. **Blocker-tag:** db-write-blocked"),
        entry("- 2026-09-01T00:40:00Z: item 23 shipped as PR #501."),
        entry("- 2026-09-01T01:00:00Z: item 24 blocked. **Blocker-tag:** db-write-blocked"),
    ]
    tags = health.compute_recent_blocker_tags(entries)

    assert tags == ["db-write-blocked", "db-write-blocked", "db-write-blocked"]
    assert health.compute_repeated_without_progress(tags) is True


# --- compute_blocker_class -----------------------------------------------------


def test_blocker_class_uses_last_entrys_own_tag_when_present():
    entries = [entry("- 2026-09-01T00:00:00Z: item 1 blocked. **Blocker-tag:** foo")]

    assert health.compute_blocker_class(entries, paused=False) == "foo"


def test_blocker_class_falls_back_to_history_while_paused():
    entries = [
        entry("- 2026-09-01T00:00:00Z: item 1 blocked. **Blocker-tag:** foo"),
        entry("- 2026-09-01T01:00:00Z: **PAUSED after the last 3 ticks blocked on foo. Backing off.**"),
        entry("- 2026-09-01T02:00:00Z: **Still paused — probe found foo unchanged.**"),
    ]

    assert health.compute_blocker_class(entries, paused=True) == "foo"


def test_blocker_class_none_when_not_blocked_and_not_paused():
    entries = [entry("- 2026-09-01T00:00:00Z: item 1 shipped as PR #1.")]

    assert health.compute_blocker_class(entries, paused=False) is None


# --- compute_last_successful_tick / compute_current_item ----------------------


def test_last_successful_tick_skips_blocked_and_bookkeeping_entries():
    entries = [
        entry("- 2026-09-01T00:00:00Z: item 1 shipped as PR #1."),
        entry("- 2026-09-01T01:00:00Z: item 2 blocked. **Blocker-tag:** foo"),
        entry("- 2026-09-01T02:00:00Z: **PAUSED after the last 3 ticks blocked on foo. Backing off.**"),
    ]

    assert health.compute_last_successful_tick(entries) == "2026-09-01T00:00:00Z"


def test_current_item_prefers_most_recent_entrys_own_mention():
    entries = [entry("- 2026-09-01T00:00:00Z: item 42 shipped as PR #1.")]

    assert health.compute_current_item(entries, []) == 42


def test_current_item_falls_back_to_first_non_terminal_backlog_item():
    lines = [
        "1. **DONE (PR #1, merged 2026-01-01) — First item.**\n",
        "2. **Second item, still open.**\n",
    ]

    assert health.compute_current_item([], lines) == 2


# --- compute_last_tick_outcome -------------------------------------------------


def test_last_tick_outcome_progress_on_shipped_entry():
    entries = [entry("- 2026-09-01T00:00:00Z: item 1 merged as PR #12; both /review-branch passes clean.")]

    assert health.compute_last_tick_outcome(entries) == "progress"


def test_last_tick_outcome_progress_on_resumed_shipped_entry():
    entries = [entry("- 2026-09-01T00:00:00Z: item 1 shipped as PR #12 (resumed from an interrupted prior run).")]

    assert health.compute_last_tick_outcome(entries) == "progress"


def test_last_tick_outcome_progress_on_ordinary_item_skip_with_no_tag():
    entries = [
        entry(
            "- 2026-09-01T00:00:00Z: item 3 has leftover commits from closed PR #9; not resumed — "
            "delete the branch or reopen the PR by hand."
        )
    ]

    assert health.compute_last_tick_outcome(entries) == "progress"


def test_last_tick_outcome_idle_on_nothing_actionable():
    entries = [entry("- 2026-09-01T00:00:00Z: nothing actionable this run.")]

    assert health.compute_last_tick_outcome(entries) == "idle"


def test_last_tick_outcome_blocked_on_blocker_tag():
    entries = [entry("- 2026-09-01T00:00:00Z: item 2 blocked. **Blocker-tag:** db-write-blocked")]

    assert health.compute_last_tick_outcome(entries) == "blocked"


def test_last_tick_outcome_paused_on_paused_bookkeeping():
    entries = [entry("- 2026-09-01T00:00:00Z: **PAUSED after the last 3 ticks blocked on foo. Backing off.**")]

    assert health.compute_last_tick_outcome(entries) == "paused"


def test_last_tick_outcome_paused_on_still_paused_bookkeeping():
    entries = [entry("- 2026-09-01T00:00:00Z: **Still paused — probe found foo unchanged.**")]

    assert health.compute_last_tick_outcome(entries) == "paused"


def test_last_tick_outcome_unknown_on_bare_resumed_entry():
    entries = [entry("- 2026-09-01T00:00:00Z: **RESUMED — foo cleared (paused since 2026-08-31T00:00:00Z).**")]

    assert health.compute_last_tick_outcome(entries) == "unknown"


def test_last_tick_outcome_unknown_on_empty_log():
    assert health.compute_last_tick_outcome([]) == "unknown"


def test_last_tick_outcome_only_considers_most_recent_entry():
    entries = [
        entry("- 2026-09-01T00:00:00Z: item 2 blocked. **Blocker-tag:** db-write-blocked"),
        entry("- 2026-09-01T01:00:00Z: item 3 merged as PR #7."),
    ]

    assert health.compute_last_tick_outcome(entries) == "progress"


# --- parse_hourly_tick_interval_seconds / compute_stale_pause -----------------


def test_parse_hourly_tick_interval_seconds_recognizes_the_repos_own_timer():
    timer_text = "[Timer]\nOnCalendar=*-*-* *:03:00\nPersistent=true\n"

    assert health.parse_hourly_tick_interval_seconds(timer_text) == 3600


def test_parse_hourly_tick_interval_seconds_none_for_unrecognized_shape():
    timer_text = "[Timer]\nOnCalendar=*:0/20:00\n"

    assert health.parse_hourly_tick_interval_seconds(timer_text) is None


def test_stale_pause_false_when_within_reduced_cadence():
    paused_entry = entry("- 2026-09-01T00:00:00Z: **PAUSED after the last 3 ticks blocked on foo. Backing off.**")
    now = datetime(2026, 9, 1, 1, 0, 0, tzinfo=timezone.utc)  # 1h elapsed, threshold well above that

    assert (
        health.compute_stale_pause(
            paused=True, paused_entry=paused_entry, now=now, reduced_cadence_seconds=10800, buffer_multiplier=1.5
        )
        is False
    )


def test_stale_pause_true_once_past_reduced_cadence_plus_buffer():
    paused_entry = entry("- 2026-09-01T00:00:00Z: **PAUSED after the last 3 ticks blocked on foo. Backing off.**")
    now = datetime(2026, 9, 1, 6, 0, 0, tzinfo=timezone.utc)  # 6h elapsed > 10800s * 1.5 = 4.5h

    assert (
        health.compute_stale_pause(
            paused=True, paused_entry=paused_entry, now=now, reduced_cadence_seconds=10800, buffer_multiplier=1.5
        )
        is True
    )


def test_stale_pause_false_when_not_paused():
    now = datetime(2026, 9, 1, 6, 0, 0, tzinfo=timezone.utc)

    assert (
        health.compute_stale_pause(
            paused=False, paused_entry=None, now=now, reduced_cadence_seconds=10800, buffer_multiplier=1.5
        )
        is False
    )


# --- build_report / format_shell (end to end) ---------------------------------


def write_fixture(tmp_path: Path, status_log: str) -> tuple[Path, Path]:
    next_task = tmp_path / "NEXT_TASK.md"
    next_task.write_text(
        "# Refactor backlog\n\n1. **DONE (PR #1, merged 2026-01-01) — First item.**\n2. **Second item.**\n\n"
        "# Status log\n\n" + status_log,
        encoding="utf-8",
    )
    timer = tmp_path / "claude-loop.timer"
    timer.write_text("[Timer]\nOnCalendar=*-*-* *:03:00\n", encoding="utf-8")
    return next_task, timer


def test_build_report_clean_state(tmp_path):
    next_task, timer = write_fixture(tmp_path, "- 2026-09-01T00:00:00Z: item 1 shipped as PR #1.\n")

    report = health.build_report(
        next_task_path=next_task,
        timer_path=timer,
        tick_interval_seconds=None,
        probe_multiplier=3.0,
        stale_pause_buffer=1.5,
        now=datetime(2026, 9, 1, 1, 0, 0, tzinfo=timezone.utc),
    )

    assert report["last_successful_tick"] == "2026-09-01T00:00:00Z"
    assert report["current_item"] == 1
    assert report["last_tick_outcome"] == "progress"
    assert report["blocker_class"] is None
    assert report["paused"] is False
    assert report["tick_interval_seconds"] == 3600
    assert report["alerts"] == {"repeated_without_progress": False, "stale_pause": False}


def test_build_report_flags_repeated_without_progress(tmp_path):
    status_log = (
        "- 2026-09-01T00:00:00Z: item 2 blocked. **Blocker-tag:** db-write-blocked\n"
        "- 2026-09-01T00:20:00Z: item 2 blocked. **Blocker-tag:** db-write-blocked\n"
        "- 2026-09-01T00:40:00Z: item 2 blocked. **Blocker-tag:** db-write-blocked\n"
    )
    next_task, timer = write_fixture(tmp_path, status_log)

    report = health.build_report(
        next_task_path=next_task,
        timer_path=timer,
        tick_interval_seconds=None,
        probe_multiplier=3.0,
        stale_pause_buffer=1.5,
        now=datetime(2026, 9, 1, 1, 0, 0, tzinfo=timezone.utc),
    )

    assert report["alerts"]["repeated_without_progress"] is True
    assert report["blocker_class"] == "db-write-blocked"


def test_build_report_flags_stale_pause(tmp_path):
    status_log = "- 2026-09-01T00:00:00Z: **PAUSED after the last 3 ticks blocked on foo. Backing off.**\n"
    next_task, timer = write_fixture(tmp_path, status_log)

    report = health.build_report(
        next_task_path=next_task,
        timer_path=timer,
        tick_interval_seconds=None,
        probe_multiplier=3.0,
        stale_pause_buffer=1.5,
        now=datetime(2026, 9, 1, 6, 0, 0, tzinfo=timezone.utc),
    )

    assert report["paused"] is True
    assert report["alerts"]["stale_pause"] is True


def test_format_shell_quotes_values_and_covers_every_field(tmp_path):
    next_task, timer = write_fixture(tmp_path, "- 2026-09-01T00:00:00Z: item 1 shipped as PR #1.\n")
    report = health.build_report(
        next_task_path=next_task,
        timer_path=timer,
        tick_interval_seconds=None,
        probe_multiplier=3.0,
        stale_pause_buffer=1.5,
        now=datetime(2026, 9, 1, 1, 0, 0, tzinfo=timezone.utc),
    )

    rendered = health.format_shell(report)

    assert "VPS_LOOP_LAST_SUCCESSFUL_TICK=2026-09-01T00:00:00Z" in rendered
    assert "VPS_LOOP_CURRENT_ITEM=1" in rendered
    assert "VPS_LOOP_LAST_TICK_OUTCOME=progress" in rendered
    assert "VPS_LOOP_PAUSED=false" in rendered
    assert "VPS_LOOP_REPEATED_WITHOUT_PROGRESS=false" in rendered


def test_format_shell_quotes_shell_significant_values():
    report = {
        "last_successful_tick": None,
        "current_item": None,
        "last_tick_outcome": "blocked",
        "blocker_class": "a shell-significant value; rm -rf /",
        "paused": False,
        "paused_since": None,
        "alerts": {"repeated_without_progress": False, "stale_pause": False},
    }

    rendered = health.format_shell(report)

    assert "VPS_LOOP_BLOCKER_CLASS='a shell-significant value; rm -rf /'" in rendered


# --- CLI -----------------------------------------------------------------------


def test_main_exit_code_2_when_next_task_missing(tmp_path, capsys):
    exit_code = health.main(["--repo", str(tmp_path)])

    assert exit_code == 2
    assert "does not exist" in capsys.readouterr().err


def test_main_exit_code_1_when_alert_present(tmp_path, capsys):
    next_task, timer = write_fixture(
        tmp_path,
        (
            "- 2026-09-01T00:00:00Z: item 2 blocked. **Blocker-tag:** db-write-blocked\n"
            "- 2026-09-01T00:20:00Z: item 2 blocked. **Blocker-tag:** db-write-blocked\n"
            "- 2026-09-01T00:40:00Z: item 2 blocked. **Blocker-tag:** db-write-blocked\n"
        ),
    )
    exit_code = health.main(["--repo", str(tmp_path), "--file", str(next_task), "--timer-file", str(timer)])

    assert exit_code == 1
    payload = capsys.readouterr().out
    assert '"repeated_without_progress": true' in payload


def test_main_exit_code_0_and_shell_format(tmp_path, capsys):
    next_task, timer = write_fixture(tmp_path, "- 2026-09-01T00:00:00Z: item 1 shipped as PR #1.\n")
    exit_code = health.main(
        ["--repo", str(tmp_path), "--file", str(next_task), "--timer-file", str(timer), "--format", "shell"]
    )

    assert exit_code == 0
    assert "VPS_LOOP_CURRENT_ITEM=1" in capsys.readouterr().out
