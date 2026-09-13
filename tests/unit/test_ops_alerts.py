"""Tests for scripts/ops_alerts.py: anomaly-only alerting with deduplication on
top of scripts/ops_status_page.py's combined operations-status document.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from scripts import ops_alerts
from scripts.ops_alerts import (
    AlertState,
    ComponentAlertState,
    build_notification,
    check_monitor_silence,
    evaluate_components,
    load_state,
    save_state,
)

T0 = datetime(2026, 9, 13, 12, 0, 0, tzinfo=timezone.utc)


def make_document(states: dict[str, str], *, reasons: dict[str, str] | None = None, now: datetime = T0) -> dict:
    """A minimal stand-in for ops_status_page.build_document's own shape --
    only the fields evaluate_components actually reads."""

    return {
        "schema_version": 1,
        "generated_at": now.isoformat(),
        "overall_state": max(states.values(), default="healthy"),
        "components": [
            {"component": name, "state": state, "age_seconds": 120, "last_success_at": None}
            for name, state in states.items()
        ],
        "reasons": reasons or {},
    }


# ── transition into a bad state ────────────────────────────────────────────


def test_first_ever_bad_observation_alerts_as_entered():
    document = make_document({"vps_loop": "failed"}, reasons={"vps_loop": "circuit-breaker paused"})
    alerts, updated = evaluate_components(document, AlertState(), now=T0)

    assert len(alerts) == 1
    assert alerts[0].component == "vps_loop"
    assert alerts[0].kind == "entered"
    assert alerts[0].state == "failed"
    assert alerts[0].reason == "circuit-breaker paused"
    assert updated["vps_loop"].last_alerted_state == "failed"
    assert updated["vps_loop"].last_alert_at == ops_alerts._isoformat(T0)


def test_healthy_to_healthy_never_alerts():
    state = AlertState(components={"r2": ComponentAlertState(last_observed_state="healthy")})
    document = make_document({"r2": "healthy"})

    alerts, updated = evaluate_components(document, state, now=T0)

    assert alerts == []
    assert updated["r2"].last_observed_state == "healthy"


def test_transition_from_unknown_into_bad_alerts():
    state = AlertState(components={"github": ComponentAlertState(last_observed_state="unknown")})
    document = make_document({"github": "degraded"}, reasons={"github": "1 PR(s) conflicting"})

    alerts, _updated = evaluate_components(document, state, now=T0)

    assert len(alerts) == 1
    assert alerts[0].kind == "entered"
    assert alerts[0].previous_state == "unknown"


def test_transition_to_unknown_is_never_alerted_and_preserves_bookkeeping():
    prior_alert_at = ops_alerts._isoformat(T0 - timedelta(minutes=10))
    state = AlertState(
        components={
            "oracle_crawler": ComponentAlertState(
                last_observed_state="failed", last_alerted_state="failed", last_alert_at=prior_alert_at
            )
        }
    )
    document = make_document({"oracle_crawler": "unknown"})

    alerts, updated = evaluate_components(document, state, now=T0)

    assert alerts == []
    assert updated["oracle_crawler"].last_observed_state == "unknown"
    # Bad-state bookkeeping untouched, so a later swing back to `failed` is
    # still judged against the original alert, not silently reset.
    assert updated["oracle_crawler"].last_alerted_state == "failed"
    assert updated["oracle_crawler"].last_alert_at == prior_alert_at


def test_bad_to_unknown_to_same_bad_is_dedup_suppressed_not_entered():
    # A poll landing on `unknown` mid-incident must not reset the "was this
    # already alerted on" bookkeeping: failed -> unknown -> failed again
    # (within the dedup window) is still the same ongoing incident, not a
    # fresh `entered` transition.
    prior_alert_at = ops_alerts._isoformat(T0 - timedelta(minutes=20))
    state = AlertState(
        components={
            "oracle_crawler": ComponentAlertState(
                last_observed_state="unknown", last_alerted_state="failed", last_alert_at=prior_alert_at
            )
        }
    )
    document = make_document({"oracle_crawler": "failed"})

    alerts, updated = evaluate_components(document, state, now=T0, dedup_interval_seconds=1800)

    assert alerts == []
    assert updated["oracle_crawler"].last_observed_state == "failed"
    assert updated["oracle_crawler"].last_alerted_state == "failed"
    assert updated["oracle_crawler"].last_alert_at == prior_alert_at


def test_bad_to_unknown_to_healthy_still_fires_recovered():
    # Same setup as above, but the component actually recovers instead of
    # going back to bad -- the intervening `unknown` poll must not swallow
    # the recovery alert nor silently clear state with nobody told.
    prior_alert_at = ops_alerts._isoformat(T0 - timedelta(minutes=20))
    state = AlertState(
        components={
            "oracle_crawler": ComponentAlertState(
                last_observed_state="unknown", last_alerted_state="failed", last_alert_at=prior_alert_at
            )
        }
    )
    document = make_document({"oracle_crawler": "healthy"})

    alerts, updated = evaluate_components(document, state, now=T0)

    assert len(alerts) == 1
    assert alerts[0].kind == "recovered"
    assert alerts[0].component == "oracle_crawler"
    # previous_state must reflect the real prior bad state ("failed"), not
    # the transient "unknown" that was merely last observed.
    assert alerts[0].previous_state == "failed"
    assert alerts[0].render_line() == "- oracle_crawler: recovered -> healthy (was failed)"
    assert updated["oracle_crawler"].last_alerted_state is None
    assert updated["oracle_crawler"].last_alert_at is None


# ── recovery ────────────────────────────────────────────────────────────────


def test_recovery_alerts_after_a_prior_alert():
    state = AlertState(
        components={
            "vps_loop": ComponentAlertState(
                last_observed_state="failed",
                last_alerted_state="failed",
                last_alert_at=ops_alerts._isoformat(T0 - timedelta(minutes=5)),
            )
        }
    )
    document = make_document({"vps_loop": "healthy"})

    alerts, updated = evaluate_components(document, state, now=T0)

    assert len(alerts) == 1
    assert alerts[0].kind == "recovered"
    assert alerts[0].state == "healthy"
    assert alerts[0].previous_state == "failed"
    assert updated["vps_loop"].last_alerted_state is None
    assert updated["vps_loop"].last_alert_at is None


def test_no_recovery_alert_when_never_previously_alerted():
    # A component that was observed bad (e.g. its very first-ever poll landed
    # mid-incident, before this alerter existed) but was never itself alerted
    # on cannot "recover" in a way anyone was told to worry about.
    state = AlertState(components={"r2": ComponentAlertState(last_observed_state="degraded")})
    document = make_document({"r2": "healthy"})

    alerts, updated = evaluate_components(document, state, now=T0)

    assert alerts == []
    assert updated["r2"].last_observed_state == "healthy"


# ── ongoing bad state: escalation vs. deduplication ─────────────────────────


def test_escalation_alerts_immediately_even_within_dedup_window():
    state = AlertState(
        components={
            "r2": ComponentAlertState(
                last_observed_state="degraded",
                last_alerted_state="degraded",
                last_alert_at=ops_alerts._isoformat(T0 - timedelta(seconds=30)),
            )
        }
    )
    document = make_document({"r2": "failed"}, reasons={"r2": "disk usage is 97% (failed)"})

    alerts, updated = evaluate_components(document, state, now=T0, dedup_interval_seconds=1800)

    assert len(alerts) == 1
    assert alerts[0].kind == "escalated"
    assert alerts[0].state == "failed"
    assert updated["r2"].last_alerted_state == "failed"
    assert updated["r2"].last_alert_at == ops_alerts._isoformat(T0)


def test_same_severity_repeat_is_suppressed_within_dedup_interval():
    last_alert_at = ops_alerts._isoformat(T0 - timedelta(seconds=60))
    state = AlertState(
        components={
            "github": ComponentAlertState(
                last_observed_state="degraded", last_alerted_state="degraded", last_alert_at=last_alert_at
            )
        }
    )
    document = make_document({"github": "degraded"})

    alerts, updated = evaluate_components(document, state, now=T0, dedup_interval_seconds=1800)

    assert alerts == []
    # Suppressed poll still advances last_observed_state, but the dedup
    # clock keeps counting from the original alert.
    assert updated["github"].last_observed_state == "degraded"
    assert updated["github"].last_alert_at == last_alert_at


def test_reminder_fires_once_dedup_interval_elapses():
    last_alert_at = ops_alerts._isoformat(T0 - timedelta(seconds=1900))
    state = AlertState(
        components={
            "github": ComponentAlertState(
                last_observed_state="degraded", last_alerted_state="degraded", last_alert_at=last_alert_at
            )
        }
    )
    document = make_document({"github": "degraded"}, reasons={"github": "still conflicting"})

    alerts, updated = evaluate_components(document, state, now=T0, dedup_interval_seconds=1800)

    assert len(alerts) == 1
    assert alerts[0].kind == "reminder"
    assert updated["github"].last_alert_at == ops_alerts._isoformat(T0)


def test_dropping_severity_while_still_bad_is_suppressed_not_escalated():
    # failed -> degraded is still "bad", but a decrease in severity is not an
    # escalation, so it is subject to the same dedup window as any repeat.
    state = AlertState(
        components={
            "oracle_crawler": ComponentAlertState(
                last_observed_state="failed",
                last_alerted_state="failed",
                last_alert_at=ops_alerts._isoformat(T0 - timedelta(seconds=30)),
            )
        }
    )
    document = make_document({"oracle_crawler": "degraded"})

    alerts, updated = evaluate_components(document, state, now=T0, dedup_interval_seconds=1800)

    assert alerts == []
    assert updated["oracle_crawler"].last_observed_state == "degraded"
    assert updated["oracle_crawler"].last_alerted_state == "failed"


# ── grouping ─────────────────────────────────────────────────────────────


def test_multiple_simultaneous_transitions_are_grouped_into_one_notification():
    document = make_document(
        {"vps_loop": "failed", "github": "healthy", "r2": "stale"},
        reasons={"vps_loop": "circuit-breaker paused", "r2": "disk usage is 95% (stale)"},
    )

    alerts, _updated = evaluate_components(document, AlertState(), now=T0)
    notification = build_notification(alerts, None, now=T0)

    assert {a.component for a in notification.component_alerts} == {"vps_loop", "r2"}
    assert not notification.is_empty
    text = notification.render_text()
    assert "vps_loop" in text and "r2" in text
    assert text.count("\n- ") == 2  # sanity: one line per component alert


# ── monitor silence ──────────────────────────────────────────────────────


def test_monitor_silence_not_flagged_on_first_ever_run():
    assert check_monitor_silence(last_run_at=None, now=T0, max_silence_seconds=3600) is None


def test_monitor_silence_not_flagged_within_threshold():
    last_run_at = T0 - timedelta(seconds=1000)
    assert check_monitor_silence(last_run_at=last_run_at, now=T0, max_silence_seconds=3600) is None


def test_monitor_silence_flagged_once_gap_exceeds_threshold():
    last_run_at = T0 - timedelta(seconds=7200)
    alert = check_monitor_silence(last_run_at=last_run_at, now=T0, max_silence_seconds=3600)

    assert alert is not None
    assert alert.elapsed_seconds == 7200
    assert "silent for 7200s" in alert.render_line()

    notification = build_notification([], alert, now=T0)
    assert not notification.is_empty
    assert "ops_alerts monitor" in notification.render_text()


# ── state persistence ────────────────────────────────────────────────────


def test_state_round_trips_through_disk(tmp_path: Path):
    path = tmp_path / "alert-state.json"
    state = AlertState(
        last_run_at=ops_alerts._isoformat(T0),
        components={
            "vps_loop": ComponentAlertState(
                last_observed_state="failed", last_alerted_state="failed", last_alert_at=ops_alerts._isoformat(T0)
            )
        },
    )

    save_state(path, state)
    loaded = load_state(path)

    assert loaded.last_run_at == state.last_run_at
    assert loaded.components["vps_loop"] == state.components["vps_loop"]


def test_load_state_missing_file_returns_empty_state(tmp_path: Path):
    state = load_state(tmp_path / "does-not-exist.json")
    assert state.last_run_at is None
    assert state.components == {}


def test_load_state_corrupt_json_returns_empty_state(tmp_path: Path):
    path = tmp_path / "alert-state.json"
    path.write_text("not json{{{", encoding="utf-8")

    state = load_state(path)

    assert state.last_run_at is None
    assert state.components == {}


def test_load_state_rejects_foreign_schema_version(tmp_path: Path):
    path = tmp_path / "alert-state.json"
    path.write_text(json.dumps({"schema_version": 999, "last_run_at": "x", "components": {}}), encoding="utf-8")

    state = load_state(path)

    assert state.last_run_at is None
    assert state.components == {}


# ── CLI wiring ────────────────────────────────────────────────────────────


def test_main_exits_zero_and_writes_heartbeat_on_a_quiet_poll(tmp_path: Path, monkeypatch, capsys):
    state_path = tmp_path / "alert-state.json"
    document = make_document({"vps_loop": "healthy", "github": "healthy", "oracle_crawler": "healthy", "r2": "healthy"})
    monkeypatch.setattr(ops_alerts.ops_status_page, "collect_all", lambda **kw: [])
    monkeypatch.setattr(ops_alerts.ops_status_page, "build_document", lambda *a, **kw: document)

    exit_code = ops_alerts.main(["--state-path", str(state_path), "--repo", str(tmp_path)])

    assert exit_code == 0
    assert "no anomalies" in capsys.readouterr().out
    assert state_path.exists()
    assert json.loads(state_path.read_text())["last_run_at"] is not None


def test_main_exits_nonzero_and_prints_alert_on_a_new_failure(tmp_path: Path, monkeypatch, capsys):
    state_path = tmp_path / "alert-state.json"
    document = make_document({"vps_loop": "failed"}, reasons={"vps_loop": "circuit-breaker paused"})
    monkeypatch.setattr(ops_alerts.ops_status_page, "collect_all", lambda **kw: [])
    monkeypatch.setattr(ops_alerts.ops_status_page, "build_document", lambda *a, **kw: document)

    exit_code = ops_alerts.main(["--state-path", str(state_path), "--repo", str(tmp_path)])

    out = capsys.readouterr().out
    assert exit_code == 1
    assert "ALERT" in out
    assert "vps_loop" in out


def test_main_reports_monitor_silence_from_a_stale_state_file(tmp_path: Path, monkeypatch, capsys):
    state_path = tmp_path / "alert-state.json"
    # main() reads the wall clock directly (it is the CLI entry point, not a
    # pure function), so the staleness baseline is relative to real "now"
    # rather than the fixed T0 fixture used elsewhere in this file.
    stale_last_run = ops_alerts._isoformat(datetime.now(timezone.utc) - timedelta(hours=3))
    save_state(state_path, AlertState(last_run_at=stale_last_run))
    document = make_document({"vps_loop": "healthy"})
    monkeypatch.setattr(ops_alerts.ops_status_page, "collect_all", lambda **kw: [])
    monkeypatch.setattr(ops_alerts.ops_status_page, "build_document", lambda *a, **kw: document)

    exit_code = ops_alerts.main(
        ["--state-path", str(state_path), "--repo", str(tmp_path), "--monitor-max-silence-seconds", "3600"]
    )

    out = capsys.readouterr().out
    assert exit_code == 1
    assert "ops_alerts monitor" in out
    assert "silent for" in out


# ── ping delivery ───────────────────────────────────────────────────────────


def test_deliver_ping_is_a_noop_when_url_unset():
    calls = []
    ops_alerts._deliver_ping(None, ok=True, body="quiet", post=lambda url, body: calls.append((url, body)))
    assert calls == []


def test_deliver_ping_posts_to_bare_url_on_ok():
    calls = []
    ops_alerts._deliver_ping(
        "https://example.test/ping/abc", ok=True, body="quiet", post=lambda url, body: calls.append((url, body))
    )
    assert calls == [("https://example.test/ping/abc", "quiet")]


def test_deliver_ping_posts_to_fail_suffix_on_anomaly():
    calls = []
    ops_alerts._deliver_ping(
        "https://example.test/ping/abc", ok=False, body="ALERT", post=lambda url, body: calls.append((url, body))
    )
    assert calls == [("https://example.test/ping/abc/fail", "ALERT")]


def test_deliver_ping_strips_trailing_slash_before_appending_fail():
    calls = []
    ops_alerts._deliver_ping(
        "https://example.test/ping/abc/", ok=False, body="ALERT", post=lambda url, body: calls.append((url, body))
    )
    assert calls == [("https://example.test/ping/abc/fail", "ALERT")]


def test_deliver_ping_swallows_a_delivery_failure(capsys):
    def _raise(url, body):
        raise OSError("network unreachable")

    # Must not raise -- a delivery failure is logged, never allowed to crash a
    # poll that otherwise completed successfully or change its exit code.
    ops_alerts._deliver_ping("https://example.test/ping/abc", ok=True, body="quiet", post=_raise)
    assert "failed to deliver ping" in capsys.readouterr().err


def test_main_delivers_ok_ping_on_a_quiet_poll(tmp_path: Path, monkeypatch):
    state_path = tmp_path / "alert-state.json"
    document = make_document({"vps_loop": "healthy"})
    monkeypatch.setattr(ops_alerts.ops_status_page, "collect_all", lambda **kw: [])
    monkeypatch.setattr(ops_alerts.ops_status_page, "build_document", lambda *a, **kw: document)
    monkeypatch.setenv(ops_alerts.PING_URL_ENV_VAR, "https://example.test/ping/quiet")
    calls = []
    monkeypatch.setattr(ops_alerts, "_http_post", lambda url, body: calls.append((url, body)))

    exit_code = ops_alerts.main(["--state-path", str(state_path), "--repo", str(tmp_path)])

    assert exit_code == 0
    assert len(calls) == 1
    assert calls[0][0] == "https://example.test/ping/quiet"


def test_main_delivers_fail_ping_on_an_anomaly(tmp_path: Path, monkeypatch):
    state_path = tmp_path / "alert-state.json"
    document = make_document({"vps_loop": "failed"}, reasons={"vps_loop": "circuit-breaker paused"})
    monkeypatch.setattr(ops_alerts.ops_status_page, "collect_all", lambda **kw: [])
    monkeypatch.setattr(ops_alerts.ops_status_page, "build_document", lambda *a, **kw: document)
    monkeypatch.setenv(ops_alerts.PING_URL_ENV_VAR, "https://example.test/ping/quiet")
    calls = []
    monkeypatch.setattr(ops_alerts, "_http_post", lambda url, body: calls.append((url, body)))

    exit_code = ops_alerts.main(["--state-path", str(state_path), "--repo", str(tmp_path)])

    assert exit_code == 1
    assert len(calls) == 1
    assert calls[0][0] == "https://example.test/ping/quiet/fail"
    assert "vps_loop" in calls[0][1]


def test_main_delivers_no_ping_when_url_unset(tmp_path: Path, monkeypatch):
    state_path = tmp_path / "alert-state.json"
    document = make_document({"vps_loop": "healthy"})
    monkeypatch.setattr(ops_alerts.ops_status_page, "collect_all", lambda **kw: [])
    monkeypatch.setattr(ops_alerts.ops_status_page, "build_document", lambda *a, **kw: document)
    monkeypatch.delenv(ops_alerts.PING_URL_ENV_VAR, raising=False)
    calls = []
    monkeypatch.setattr(ops_alerts, "_http_post", lambda url, body: calls.append((url, body)))

    exit_code = ops_alerts.main(["--state-path", str(state_path), "--repo", str(tmp_path)])

    assert exit_code == 0
    assert calls == []
