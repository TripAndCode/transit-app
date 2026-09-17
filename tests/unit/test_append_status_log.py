"""Tests for the crash-safe, lock-guarded NEXT_TASK.md Status log appender.

See `scripts/append_status_log.py`'s own module docstring for why item 141
(an interactive `/vps-loop-run` session racing the cron-triggered
`claude-loop.service` tick's own Status log append) needs a dedicated lock
file rather than reusing `deploy/vps/claude-loop.sh`'s own
`/tmp/claude-loop.lock`, and why a real lock was chosen over a PID-file or a
lock-free retry protocol.
"""

from __future__ import annotations

import importlib.util
import os
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "append_status_log.py"
SPEC = importlib.util.spec_from_file_location("append_status_log", SCRIPT)
assert SPEC and SPEC.loader
mod = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = mod
SPEC.loader.exec_module(mod)


NOW = datetime(2026, 9, 18, 12, 0, 0, tzinfo=timezone.utc)

BASE_FILE = """# Refactor backlog (worked one item per run, via /vps-loop-run)

1. **Some item.** Detail text.

# Status log

- 2026-09-18T10:00:00Z: nothing actionable this run.
"""


@pytest.fixture()
def next_task(tmp_path: Path) -> Path:
    path = tmp_path / "NEXT_TASK.md"
    path.write_text(BASE_FILE, encoding="utf-8")
    return path


@pytest.fixture()
def lock_file(tmp_path: Path) -> Path:
    return tmp_path / "status-log.lock"


# --- format_entry_block -------------------------------------------------


def test_format_entry_block_indents_bare_continuation_lines():
    block = mod.format_entry_block(
        "item 5 blocked before verification.\nFindings: null handling.\n**Blocker-tag:** worker-blocked",
        timestamp="2026-09-18T12:00:00Z",
    )

    lines = block.splitlines()
    assert lines[0] == "- 2026-09-18T12:00:00Z: item 5 blocked before verification."
    assert lines[1] == "  Findings: null handling."
    assert lines[2] == "  **Blocker-tag:** worker-blocked"


def test_format_entry_block_preserves_already_indented_lines_and_blank_lines():
    block = mod.format_entry_block("first line.\n\n  already indented.", timestamp="2026-09-18T12:00:00Z")

    lines = block.splitlines()
    assert lines[1] == ""
    assert lines[2] == "  already indented."


# --- find_status_log_section_bounds -------------------------------------


def test_find_status_log_section_bounds_ends_at_file_end_when_last_section():
    lines = BASE_FILE.splitlines()
    heading_index, end_index = mod.find_status_log_section_bounds(lines)

    assert lines[heading_index] == "# Status log"
    assert end_index == len(lines)


def test_find_status_log_section_bounds_stops_before_a_later_top_level_heading():
    lines = (BASE_FILE + "\n# Appendix\n\nsome trailing top-level section\n").splitlines()
    heading_index, end_index = mod.find_status_log_section_bounds(lines)

    assert lines[end_index] == "# Appendix"
    assert heading_index < end_index


def test_find_status_log_section_bounds_raises_without_heading():
    with pytest.raises(mod.AppendStatusLogError):
        mod.find_status_log_section_bounds(["# Refactor backlog", "1. **Item.**"])


# --- append_entry ---------------------------------------------------------


def test_append_entry_writes_new_entry_at_end_of_status_log(next_task: Path):
    appended, block = mod.append_entry(next_task, "item 7 shipped as PR #501.", now=NOW)

    assert appended is True
    text = next_task.read_text(encoding="utf-8")
    assert text.endswith("- 2026-09-18T12:00:00Z: item 7 shipped as PR #501.\n")
    assert "- 2026-09-18T10:00:00Z: nothing actionable this run." in text
    assert block == "- 2026-09-18T12:00:00Z: item 7 shipped as PR #501."


def test_append_entry_skip_if_tail_startswith_matches_does_not_write(next_task: Path):
    original = next_task.read_text(encoding="utf-8")

    appended, remainder = mod.append_entry(
        next_task, "nothing actionable this run.", now=NOW, skip_if_tail_startswith="nothing actionable this run."
    )

    assert appended is False
    assert remainder == "nothing actionable this run."
    assert next_task.read_text(encoding="utf-8") == original


def test_append_entry_skip_if_tail_startswith_no_match_still_writes(next_task: Path):
    appended, _ = mod.append_entry(next_task, "item 8 shipped.", now=NOW, skip_if_tail_startswith="nonexistent marker")

    assert appended is True
    assert "item 8 shipped." in next_task.read_text(encoding="utf-8")


def test_append_entry_is_atomic_write_not_in_place_truncate(next_task: Path, monkeypatch: pytest.MonkeyPatch):
    # A crash mid-write must never leave a half-written NEXT_TASK.md. Confirm
    # the implementation goes through tempfile+os.replace (a partial write to
    # the temp file leaves the original untouched) rather than open(path, "a").
    original = next_task.read_text(encoding="utf-8")
    real_replace = os.replace
    calls = []

    def spy_replace(src, dst):
        calls.append((src, dst))
        return real_replace(src, dst)

    monkeypatch.setattr(mod.os, "replace", spy_replace)
    mod.append_entry(next_task, "item 9 shipped.", now=NOW)

    assert len(calls) == 1
    assert next_task.read_text(encoding="utf-8") != original


# --- held_lock -------------------------------------------------------------


def test_held_lock_serializes_two_callers(lock_file: Path):
    order: list[str] = []

    def hold_then_release(name: str, hold_seconds: float) -> None:
        with mod.held_lock(lock_file, timeout_seconds=5.0):
            order.append(f"{name}-start")
            time.sleep(hold_seconds)
            order.append(f"{name}-end")

    first = threading.Thread(target=hold_then_release, args=("A", 0.3))
    first.start()
    time.sleep(0.05)  # ensure A acquires first
    second = threading.Thread(target=hold_then_release, args=("B", 0.0))
    second.start()
    first.join(timeout=5)
    second.join(timeout=5)

    # B must never start until A has fully finished (released the lock) --
    # this is the "one lock holder, one waiter" property the fix guarantees.
    assert order == ["A-start", "A-end", "B-start", "B-end"]


def test_held_lock_raises_timeout_when_already_held(lock_file: Path):
    holder_ready = threading.Event()
    release = threading.Event()

    def hold_forever() -> None:
        with mod.held_lock(lock_file, timeout_seconds=5.0):
            holder_ready.set()
            release.wait(timeout=5)

    holder = threading.Thread(target=hold_forever)
    holder.start()
    assert holder_ready.wait(timeout=5)

    start = time.monotonic()
    with pytest.raises(mod.LockTimeoutError):
        with mod.held_lock(lock_file, timeout_seconds=0.2, poll_interval_seconds=0.02):
            pass
    elapsed = time.monotonic() - start

    assert 0.2 <= elapsed < 2.0
    release.set()
    holder.join(timeout=5)


# --- run() / concurrency end-to-end -----------------------------------------


def test_run_end_to_end_generates_timestamp_and_appends(next_task: Path, lock_file: Path):
    result = mod.run(
        next_task_path=next_task,
        entry_text="item 10 shipped as PR #502.",
        lock_path=lock_file,
        lock_timeout_seconds=5.0,
        now=NOW,
    )

    assert result["appended"] is True
    assert "2026-09-18T12:00:00Z" in result["entry"]
    assert "item 10 shipped as PR #502." in next_task.read_text(encoding="utf-8")


def test_run_missing_next_task_raises(tmp_path: Path, lock_file: Path):
    with pytest.raises(mod.AppendStatusLogError):
        mod.run(
            next_task_path=tmp_path / "missing.md",
            entry_text="anything",
            lock_path=lock_file,
            lock_timeout_seconds=5.0,
            now=NOW,
        )


def test_two_concurrent_normal_appends_never_interleave_or_corrupt(next_task: Path, lock_file: Path):
    """The core race this item closes: two writers must serialize, never interleave."""

    results: dict[str, tuple[bool, str]] = {}
    real_write = mod._atomic_write

    def slow_write(delay: float, path, content):
        time.sleep(delay)
        real_write(path, content)

    # Both threads race to acquire the same lock at nearly the same instant;
    # whichever wins holds it (including through its own deliberately slowed
    # write, reassigned only while the lock is held, so the two threads never
    # touch the module-level hook concurrently) while the other blocks on
    # `flock` and only proceeds afterward -- proving one holder + one waiter,
    # never two simultaneous writers.
    def run_with_lock(name: str, delay: float) -> None:
        with mod.held_lock(lock_file, timeout_seconds=5.0):
            mod._atomic_write = lambda path, content, d=delay: slow_write(d, path, content)  # type: ignore[assignment]
            try:
                appended, detail = mod.append_entry(next_task, f"item from {name}.", now=NOW)
            finally:
                mod._atomic_write = real_write  # type: ignore[assignment]
            results[name] = (appended, detail)

    threads = [
        threading.Thread(target=run_with_lock, args=("A", 0.2)),
        threading.Thread(target=run_with_lock, args=("B", 0.0)),
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=5)

    text = next_task.read_text(encoding="utf-8")
    assert "item from A." in text
    assert "item from B." in text
    assert results["A"][0] is True
    assert results["B"][0] is True
    # No torn/interleaved write: exactly the original entry plus both new
    # ones, each on its own well-formed bullet line.
    entry_lines = [line for line in text.splitlines() if line.startswith("- ")]
    assert len(entry_lines) == 3


def test_two_concurrent_idle_throttle_appends_produce_only_one_idle_entry(tmp_path: Path, lock_file: Path):
    """Closes the duplicate_idle_tail race: both callers believe the tail isn't idle yet."""

    idle_text = "nothing actionable this run."
    # Tail starts as a real, non-idle entry: both callers' own pre-lock read
    # (mirroring Step 3's own check before requesting this append) would
    # have seen no reason to skip -- exactly the shape of the pre-fix race,
    # where the actual skip decision must instead be made fresh, under the
    # lock, against whatever the *other* caller may have just written.
    next_task = tmp_path / "NEXT_TASK.md"
    next_task.write_text(
        "# Refactor backlog (worked one item per run, via /vps-loop-run)\n"
        "\n"
        "1. **Some item.** Detail text.\n"
        "\n"
        "# Status log\n"
        "\n"
        "- 2026-09-18T09:00:00Z: item 3 shipped as PR #400.\n",
        encoding="utf-8",
    )
    appended_flags: list[bool] = []
    lock = threading.Lock()
    barrier = threading.Barrier(2)

    def attempt() -> None:
        barrier.wait(timeout=5)
        result = mod.run(
            next_task_path=next_task,
            entry_text=idle_text,
            lock_path=lock_file,
            lock_timeout_seconds=5.0,
            now=NOW,
            skip_if_tail_startswith=idle_text,
        )
        with lock:
            appended_flags.append(bool(result["appended"]))

    # Both callers read a not-yet-idle tail before this test starts (the
    # fixture's tail is a real progress-shaped entry, not idle), so at
    # decision time neither would have skipped on its own -- exactly the
    # shape of the pre-fix race.
    threads = [threading.Thread(target=attempt) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=5)

    assert sorted(appended_flags) == [False, True]
    text = next_task.read_text(encoding="utf-8")
    assert text.count(f": {idle_text}") == 1
