"""Tests for the VPS loop's guarded-continuation (chained-tick) state machine."""

from __future__ import annotations

import importlib.util
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "vps_loop_chain_state.py"
WRAPPER_SCRIPT = ROOT / "deploy" / "vps" / "claude-loop.sh"
SPEC = importlib.util.spec_from_file_location("vps_loop_chain_state", SCRIPT)
assert SPEC and SPEC.loader
chain = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = chain
SPEC.loader.exec_module(chain)


NOW = datetime(2026, 9, 12, 12, 0, 0, tzinfo=timezone.utc)


def always_alive(pid: int) -> bool:
    return True


def never_alive(pid: int) -> bool:
    return False


# --- success chaining --------------------------------------------------------


def test_record_outcome_progress_continues_and_resets_backoff():
    state = chain.ChainState(consecutive_non_progress=2, next_earliest_attempt="2026-09-12T11:00:00Z")

    new_state, action = chain.record_outcome(state, outcome="progress", now=NOW)

    assert action == "continue"
    assert new_state.consecutive_non_progress == 0
    assert new_state.next_earliest_attempt is None
    assert new_state.in_progress is False
    assert new_state.last_outcome == "progress"


def test_gate_allows_immediately_after_progress():
    state, _ = chain.record_outcome(chain.ChainState(), outcome="progress", now=NOW)

    allowed, reason = chain.gate(state, now=NOW)

    assert allowed is True
    assert reason == "ok"


def test_chain_can_run_several_progress_ticks_back_to_back():
    state = chain.ChainState()
    for _ in range(3):
        state = chain.begin(state, pid=123, now=NOW)
        state, action = chain.record_outcome(state, outcome="progress", now=NOW)
        assert action == "continue"
        allowed, _ = chain.gate(state, now=NOW)
        assert allowed is True


# --- failure stop + backoff --------------------------------------------------


def test_record_outcome_blocked_stops_and_schedules_backoff():
    state, action = chain.record_outcome(
        chain.ChainState(), outcome="blocked", now=NOW, base_seconds=300, cap_seconds=3600
    )

    assert action == "stop"
    assert state.consecutive_non_progress == 1
    assert state.last_outcome == "blocked"
    assert state.next_earliest_attempt == "2026-09-12T12:05:00Z"


def test_gate_blocks_during_backoff_window_and_allows_after():
    state, _ = chain.record_outcome(chain.ChainState(), outcome="blocked", now=NOW, base_seconds=300, cap_seconds=3600)

    still_backing_off, reason = chain.gate(state, now=NOW + timedelta(minutes=1))
    assert still_backing_off is False
    assert "backing off" in reason

    cleared, _ = chain.gate(state, now=NOW + timedelta(minutes=6))
    assert cleared is True


def test_backoff_escalates_exponentially_and_is_capped():
    state = chain.ChainState()
    for _ in range(3):
        state, action = chain.record_outcome(state, outcome="blocked", now=NOW, base_seconds=300, cap_seconds=3600)
        assert action == "stop"

    # 300 * 2**(3-1) = 1200s
    assert state.consecutive_non_progress == 3
    assert state.next_earliest_attempt == "2026-09-12T12:20:00Z"

    for _ in range(10):
        state, _ = chain.record_outcome(state, outcome="blocked", now=NOW, base_seconds=300, cap_seconds=3600)

    # Never exceeds the cap no matter how long the streak runs.
    capped_earliest = datetime.strptime(state.next_earliest_attempt, chain.TIMESTAMP_FORMAT).replace(
        tzinfo=timezone.utc
    )
    assert capped_earliest == NOW + timedelta(seconds=3600)


def test_compute_backoff_seconds_never_overflows_for_extreme_streaks():
    # A many-months-away edge case (~1000+ consecutive non-progress ticks)
    # must still return the cap rather than raising OverflowError computing
    # `2 ** exponent` uncapped.
    seconds = chain.compute_backoff_seconds(consecutive_non_progress=10_000, base_seconds=300.0, cap_seconds=3600.0)

    assert seconds == 3600.0


def test_paused_outcome_stops_like_blocked():
    state, action = chain.record_outcome(chain.ChainState(), outcome="paused", now=NOW)

    assert action == "stop"
    assert state.consecutive_non_progress == 1


def test_ambiguous_unknown_outcome_stops_and_backs_off():
    state, action = chain.record_outcome(chain.ChainState(), outcome="unknown", now=NOW)

    assert action == "stop"
    assert state.consecutive_non_progress == 1
    assert state.next_earliest_attempt is not None


# --- no-actionable-work (idle) backoff --------------------------------------


def test_idle_outcome_stops_and_backs_off_distinctly_from_blocked():
    state, action = chain.record_outcome(
        chain.ChainState(), outcome="idle", now=NOW, base_seconds=300, cap_seconds=3600
    )

    assert action == "stop"
    assert state.last_outcome == "idle"
    assert state.next_earliest_attempt is not None
    # The schedule is the same shared backoff, but the *reason* stays visible
    # and distinguishable from a real failure for health reporting.
    assert state.last_outcome != "blocked"


def test_repeated_idle_ticks_keep_backing_off_further():
    state = chain.ChainState()
    state, _ = chain.record_outcome(state, outcome="idle", now=NOW, base_seconds=60, cap_seconds=3600)
    first_wait = datetime.strptime(state.next_earliest_attempt, chain.TIMESTAMP_FORMAT)
    state, _ = chain.record_outcome(state, outcome="idle", now=NOW, base_seconds=60, cap_seconds=3600)
    second_wait = datetime.strptime(state.next_earliest_attempt, chain.TIMESTAMP_FORMAT)

    assert second_wait > first_wait


# --- stale-lock recovery -----------------------------------------------------


def test_is_stale_false_for_fresh_in_progress_tick():
    state = chain.begin(chain.ChainState(), pid=999, now=NOW)

    assert (
        chain.is_stale(state, now=NOW + timedelta(seconds=10), max_age_seconds=3300, pid_alive_fn=always_alive)
        is False
    )


def test_is_stale_true_when_pid_is_dead():
    state = chain.begin(chain.ChainState(), pid=999, now=NOW)

    assert (
        chain.is_stale(state, now=NOW + timedelta(seconds=10), max_age_seconds=3300, pid_alive_fn=never_alive)
        is True
    )


def test_is_stale_true_when_older_than_max_age_even_if_pid_alive():
    state = chain.begin(chain.ChainState(), pid=999, now=NOW)

    assert (
        chain.is_stale(state, now=NOW + timedelta(seconds=4000), max_age_seconds=3300, pid_alive_fn=always_alive)
        is True
    )


def test_is_stale_false_when_not_in_progress():
    state = chain.ChainState(in_progress=False)

    assert chain.is_stale(state, now=NOW, max_age_seconds=3300, pid_alive_fn=never_alive) is False


def test_recover_if_stale_clears_flag_and_counts_as_non_progress():
    state = chain.begin(chain.ChainState(), pid=999, now=NOW)

    recovered_state, recovered = chain.recover_if_stale(
        state, now=NOW + timedelta(seconds=10), max_age_seconds=3300, pid_alive_fn=never_alive
    )

    assert recovered is True
    assert recovered_state.in_progress is False
    assert recovered_state.pid is None
    assert recovered_state.consecutive_non_progress == 1
    assert recovered_state.last_outcome == "unknown"
    assert recovered_state.next_earliest_attempt is not None


def test_recover_if_stale_honors_custom_backoff_params():
    state = chain.begin(chain.ChainState(), pid=999, now=NOW)

    recovered_state, recovered = chain.recover_if_stale(
        state,
        now=NOW + timedelta(seconds=10),
        max_age_seconds=3300,
        base_seconds=60,
        cap_seconds=120,
        pid_alive_fn=never_alive,
    )

    assert recovered is True
    # One recovered crash is one consecutive non-progress tick: backoff_seconds
    # = min(60 * 2**0, 120) = 60s, applied on top of the `now` passed in above.
    assert recovered_state.next_earliest_attempt == "2026-09-12T12:01:10Z"


def test_recover_if_stale_no_op_when_not_stale():
    state = chain.begin(chain.ChainState(), pid=999, now=NOW)

    unchanged_state, recovered = chain.recover_if_stale(
        state, now=NOW + timedelta(seconds=5), max_age_seconds=3300, pid_alive_fn=always_alive
    )

    assert recovered is False
    assert unchanged_state == state


def test_gate_blocks_while_genuinely_in_progress():
    state = chain.begin(chain.ChainState(), pid=999, now=NOW)

    allowed, reason = chain.gate(state, now=NOW + timedelta(seconds=5))

    assert allowed is False
    assert "in-flight" in reason


# --- persistence (load/save round trip) -------------------------------------


def test_save_and_load_round_trip(tmp_path):
    path = tmp_path / "chain-state.json"
    state = chain.ChainState(
        in_progress=False,
        consecutive_non_progress=2,
        next_earliest_attempt="2026-09-12T13:00:00Z",
        last_outcome="blocked",
        last_updated="2026-09-12T12:00:00Z",
    )

    chain.save_state(path, state)
    loaded = chain.load_state(path)

    assert loaded == state


def test_load_state_missing_file_returns_fresh_default(tmp_path):
    loaded = chain.load_state(tmp_path / "does-not-exist.json")

    assert loaded == chain.ChainState()


def test_load_state_corrupt_file_returns_fresh_default(tmp_path):
    path = tmp_path / "chain-state.json"
    path.write_text("{not valid json", encoding="utf-8")

    assert chain.load_state(path) == chain.ChainState()


def test_load_state_ignores_unknown_extra_keys(tmp_path):
    path = tmp_path / "chain-state.json"
    path.write_text('{"in_progress": false, "some_future_field": 123}', encoding="utf-8")

    loaded = chain.load_state(path)

    assert loaded == chain.ChainState(in_progress=False)


# --- CLI ----------------------------------------------------------------------


def test_cli_gate_allows_fresh_state(tmp_path, capsys):
    state_file = tmp_path / "chain-state.json"

    exit_code = chain.main(
        [
            "gate",
            "--state-file",
            str(state_file),
            "--max-stale-age-seconds",
            "3300",
            "--now",
            "2026-09-12T12:00:00Z",
        ]
    )

    assert exit_code == 0
    out = capsys.readouterr().out
    assert "ALLOWED=true" in out


def test_cli_gate_recovers_stale_in_progress_and_still_reports_recovered(tmp_path, capsys):
    state_file = tmp_path / "chain-state.json"
    stale_state = chain.ChainState(in_progress=True, pid=999999999, started_at="2026-09-12T00:00:00Z")
    chain.save_state(state_file, stale_state)

    exit_code = chain.main(
        [
            "gate",
            "--state-file",
            str(state_file),
            "--max-stale-age-seconds",
            "3300",
            "--now",
            "2026-09-12T12:00:00Z",
        ]
    )

    out = capsys.readouterr().out
    assert "RECOVERED=true" in out
    # Recovery clears in_progress and counts as one non-progress tick, but the
    # freshly-scheduled backoff for that single tick is short -- gate should
    # allow again once that (or, worst case, deny with a clear reason, never
    # wedge forever).
    assert exit_code in (0, 1)
    reloaded = chain.load_state(state_file)
    assert reloaded.in_progress is False


def test_cli_gate_recovery_honors_custom_backoff_params(tmp_path, capsys):
    state_file = tmp_path / "chain-state.json"
    stale_state = chain.ChainState(in_progress=True, pid=999999999, started_at="2026-09-12T00:00:00Z")
    chain.save_state(state_file, stale_state)

    chain.main(
        [
            "gate",
            "--state-file",
            str(state_file),
            "--max-stale-age-seconds",
            "3300",
            "--backoff-base-seconds",
            "60",
            "--backoff-cap-seconds",
            "120",
            "--now",
            "2026-09-12T12:00:00Z",
        ]
    )
    capsys.readouterr()

    # The recovered crash must use the operator-configured backoff params
    # passed on the CLI, not record_outcome's own hardcoded 300s/3600s defaults.
    reloaded = chain.load_state(state_file)
    assert reloaded.next_earliest_attempt == "2026-09-12T12:01:00Z"


def test_cli_begin_then_gate_blocks(tmp_path, capsys):
    state_file = tmp_path / "chain-state.json"

    chain.main(["begin", "--state-file", str(state_file), "--pid", str(999999999), "--now", "2026-09-12T12:00:00Z"])
    capsys.readouterr()
    exit_code = chain.main(
        [
            "gate",
            "--state-file",
            str(state_file),
            "--max-stale-age-seconds",
            "3300",
            "--now",
            "2026-09-12T12:00:05Z",
        ]
    )

    assert exit_code == 1
    assert "ALLOWED=false" in capsys.readouterr().out


def test_cli_record_outcome_progress_reports_continue(tmp_path, capsys):
    state_file = tmp_path / "chain-state.json"
    chain.main(["begin", "--state-file", str(state_file), "--pid", "123", "--now", "2026-09-12T12:00:00Z"])
    capsys.readouterr()

    exit_code = chain.main(
        [
            "record-outcome",
            "--state-file",
            str(state_file),
            "--outcome",
            "progress",
            "--now",
            "2026-09-12T12:05:00Z",
        ]
    )

    assert exit_code == 0
    assert "ACTION=continue" in capsys.readouterr().out


def test_cli_record_outcome_rejects_unknown_outcome_value():
    # argparse itself enforces the `choices=` list, so an invalid value never
    # reaches record_outcome via the CLI -- covered directly at the function
    # level instead.
    with pytest.raises(ValueError):
        chain.record_outcome(chain.ChainState(), outcome="not-a-real-outcome", now=NOW)


def test_wrapper_uses_the_variable_names_record_outcome_actually_emits(tmp_path, capsys):
    """Regression guard for `deploy/vps/claude-loop.sh` reading its own
    never-assigned local placeholders instead of what `record-outcome` emits.

    The wrapper `eval`s `record-outcome`'s shell output, so it must reference
    exactly the field names that command's shell output assigns -- not a
    differently-prefixed local of its own that `eval` can never touch.
    """

    state_file = tmp_path / "chain-state.json"
    chain.main(
        [
            "record-outcome",
            "--state-file",
            str(state_file),
            "--outcome",
            "blocked",
            "--now",
            "2026-09-12T12:00:00Z",
        ]
    )
    emitted_names = {line.split("=", 1)[0] for line in capsys.readouterr().out.splitlines() if line}
    assert {"CONSECUTIVE_NON_PROGRESS", "NEXT_EARLIEST_ATTEMPT"} <= emitted_names

    wrapper_source = WRAPPER_SCRIPT.read_text(encoding="utf-8")
    assert "$CONSECUTIVE_NON_PROGRESS" in wrapper_source
    assert "$NEXT_EARLIEST_ATTEMPT" in wrapper_source
    # A CHAIN_-prefixed variant here would silently never be assigned by the
    # wrapper's `eval "$RECORD_OUTPUT"`, since record-outcome never emits it.
    assert "CHAIN_CONSECUTIVE_NON_PROGRESS" not in wrapper_source
    assert "CHAIN_NEXT_EARLIEST_ATTEMPT" not in wrapper_source


def test_wrapper_gated_exit_still_dispatches_a_heartbeat():
    """Regression guard for a gated/denied invocation silently skipping the
    heartbeat dispatch entirely.

    `gh api ... dispatches -f event_type=vps-heartbeat` only ever appears
    once in the wrapper, inside `dispatch_heartbeat()`. The gated-exit branch
    (`if [[ "$ALLOWED" != "true" ]]; then ... exit 0`) must call that
    function before its `exit 0` -- otherwise a saturated backoff (capped at
    the same interval as the systemd timer) silently settles into a
    heartbeat cadence slow enough to trip the watchdog's freshness alarm
    during ordinary idle/blocked backlog stretches, not just a genuinely
    stopped loop.
    """

    wrapper_source = WRAPPER_SCRIPT.read_text(encoding="utf-8")

    assert wrapper_source.count("gh api repos/TripAndCode/transit-app/dispatches") == 1
    dispatch_fn_start = wrapper_source.index("dispatch_heartbeat() {")
    dispatch_fn_end = wrapper_source.index("\n}", dispatch_fn_start)
    dispatch_fn_body = wrapper_source[dispatch_fn_start:dispatch_fn_end]
    assert "gh api repos/TripAndCode/transit-app/dispatches" in dispatch_fn_body

    gated_branch_start = wrapper_source.index('if [[ "$ALLOWED" != "true" ]]; then')
    gated_branch_end = wrapper_source.index("\nfi", gated_branch_start)
    gated_branch_body = wrapper_source[gated_branch_start:gated_branch_end]
    assert "dispatch_heartbeat" in gated_branch_body
    assert "exit 0" in gated_branch_body


def test_cli_show_json_reports_current_state_without_mutating(tmp_path, capsys):
    state_file = tmp_path / "chain-state.json"
    original = chain.ChainState(consecutive_non_progress=1, last_outcome="idle")
    chain.save_state(state_file, original)

    exit_code = chain.main(["show", "--state-file", str(state_file), "--format", "json"])

    assert exit_code == 0
    assert '"last_outcome": "idle"' in capsys.readouterr().out
    assert chain.load_state(state_file) == original
