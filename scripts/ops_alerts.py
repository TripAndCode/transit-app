#!/usr/bin/env python3
"""Anomaly-only alerting on top of `scripts/ops_status_page.py`'s combined
operations-status document.

The combined document already tells you the current state of all four
components on every call; polling it on a schedule and re-printing it every
time would either flood an operator with one message per poll (most of them
"still fine" or "still the same known problem") or bury the one poll that
actually matters. This module sits between the two: it keeps a small
persisted history of what was last observed and last alerted for each
component, and turns that into a short list of `ComponentAlert`s only when
something an operator should act on has actually changed:

- `entered`    -- the component just moved from a non-bad state
                  (`healthy`/`unknown`/never observed) into `degraded`,
                  `stale`, or `failed`. Always alerts; a fresh problem is
                  never suppressed by deduplication.
- `escalated`  -- the component was already in a bad state and got worse
                  (e.g. `degraded` -> `failed`). Also always alerts --
                  worsening severity is new information, not a repeat.
- `reminder`   -- the component is still in the same-or-lesser bad state as
                  last alerted, and at least `dedup_interval_seconds` has
                  passed since that alert. Ongoing outages should not go
                  silent forever, but also should not repeat every poll.
- `recovered`  -- the component left its bad state (specifically, became
                  `healthy`) after this module had previously alerted on it.
                  A component that was never alerted on cannot "recover" in
                  a way anyone was told to worry about, so that case is not
                  reported.

A transition to/from `unknown` on its own is deliberately never alerted:
`unknown` means health cannot be determined at all (see `ops_status.py`),
so treating it as either "bad" or "recovered" would be inventing a verdict
the underlying data does not support. A component's bad-state bookkeeping
(`last_alerted_state`/`last_alert_at`) is left untouched while it passes
through `unknown`, so a later swing back to a real bad state is still
correctly judged against the last thing actually alerted on.

Every component change discovered in one poll is collected into a single
`Notification` rather than raised as separate events -- one incident
touching several components (or several components degrading for
unrelated reasons at once) reaches the operator as one grouped message,
not a flood of one-line pings.

Separately, `check_monitor_silence` treats the alerting run itself as a
dead-man's switch: every invocation records `last_run_at` in the persisted
state regardless of whether anything else fired, and the next invocation
alerts if too much time has passed since that timestamp. That heartbeat
bookkeeping is intentionally the only thing written on a quiet run --
`main`'s exit code and stdout stay boring (0, one plain summary line) so
routine polling traffic never looks like a notification. A poll that finds
something worth surfacing instead prints an `ALERT`-prefixed block and
exits 1, which is the same "a scheduled run's own failure is the
notification" delivery mechanism `.github/workflows/vps-heartbeat-
watchdog.yml` already uses -- wiring this into a real paging channel is a
matter of pointing something at that exit code, not something this module
needs to know about.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Mapping, Sequence

from scripts import ops_status_page

DEFAULT_STATE_PATH = Path("/root/.ops-alert-state.json")
STATE_SCHEMA_VERSION = 1

REPO_ENV_VAR = "OPS_ALERT_REPO"
GITHUB_REPO_ENV_VAR = "OPS_ALERT_GITHUB_REPO"
STATE_PATH_ENV_VAR = "OPS_ALERT_STATE_PATH"
DEDUP_INTERVAL_ENV_VAR = "OPS_ALERT_DEDUP_INTERVAL_SECONDS"
MONITOR_MAX_SILENCE_ENV_VAR = "OPS_ALERT_MONITOR_MAX_SILENCE_SECONDS"

# Ongoing-incident reminders are spaced out enough to stay meaningfully
# different from a raw poll cadence (minutes) without going silent for the
# whole outage.
DEFAULT_DEDUP_INTERVAL_SECONDS = 1800.0
# Generous relative to any sane poll cadence (minutes), so a single missed or
# slow poll never misfires this; only a real gap in the scheduler counts.
DEFAULT_MONITOR_MAX_SILENCE_SECONDS = 3600.0

# Only these three states are ever alerted on; `healthy` and `unknown` are
# deliberately excluded because health cannot be alerted on or recovered from
# when it cannot even be determined.
BAD_STATES: frozenset[str] = frozenset({"degraded", "stale", "failed"})

# Relative severity among BAD_STATES only, matching ops_status_page's/
# status-snapshot.sh's own failed > stale > degraded ordering -- used solely
# to decide whether a bad-to-bad transition is an escalation.
_BAD_STATE_SEVERITY: Mapping[str, int] = {"degraded": 1, "stale": 2, "failed": 3}


def _isoformat(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _parse_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return None
    return parsed.astimezone(timezone.utc)


@dataclass(frozen=True)
class ComponentAlertState:
    """Per-component alert bookkeeping, persisted across polls.

    `last_observed_state` drives transition/recovery detection; the
    `last_alerted_*` pair separately tracks what was last actually reported,
    which is what escalation and deduplication compare against -- these two
    diverge whenever a bad state persists across polls without a fresh
    alert (a suppressed reminder still updates `last_observed_state` but
    not `last_alerted_state`/`last_alert_at`).
    """

    last_observed_state: str | None = None
    last_alerted_state: str | None = None
    last_alert_at: str | None = None

    def to_dict(self) -> dict:
        return {
            "last_observed_state": self.last_observed_state,
            "last_alerted_state": self.last_alerted_state,
            "last_alert_at": self.last_alert_at,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, object]) -> "ComponentAlertState":
        last_observed_state = data.get("last_observed_state")
        last_alerted_state = data.get("last_alerted_state")
        last_alert_at = data.get("last_alert_at")
        return cls(
            last_observed_state=last_observed_state if isinstance(last_observed_state, str) else None,
            last_alerted_state=last_alerted_state if isinstance(last_alerted_state, str) else None,
            last_alert_at=last_alert_at if isinstance(last_alert_at, str) else None,
        )


@dataclass
class AlertState:
    """The whole persisted state file: one `last_run_at` heartbeat timestamp
    (see `check_monitor_silence`) plus one `ComponentAlertState` per
    component last seen."""

    last_run_at: str | None = None
    components: dict[str, ComponentAlertState] = field(default_factory=dict)
    schema_version: int = STATE_SCHEMA_VERSION

    def to_dict(self) -> dict:
        return {
            "schema_version": self.schema_version,
            "last_run_at": self.last_run_at,
            "components": {name: state.to_dict() for name, state in self.components.items()},
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, object]) -> "AlertState":
        if data.get("schema_version") != STATE_SCHEMA_VERSION:
            return cls()
        last_run_at = data.get("last_run_at")
        components_raw = data.get("components")
        components: dict[str, ComponentAlertState] = {}
        if isinstance(components_raw, dict):
            for name, value in components_raw.items():
                if isinstance(name, str) and isinstance(value, dict):
                    components[name] = ComponentAlertState.from_dict(value)
        return cls(last_run_at=last_run_at if isinstance(last_run_at, str) else None, components=components)


def load_state(path: Path) -> AlertState:
    """Load persisted alert state, or a fresh empty one if the file is
    missing, unreadable, not JSON, or on an old/foreign schema version --
    corrupt or absent history must never crash the alerting run, only reset
    it to "nothing known yet"."""

    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return AlertState()
    if not isinstance(raw, dict):
        return AlertState()
    return AlertState.from_dict(raw)


def save_state(path: Path, state: AlertState) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_name(path.name + ".tmp")
    tmp_path.write_text(json.dumps(state.to_dict(), indent=2) + "\n", encoding="utf-8")
    tmp_path.replace(path)


@dataclass(frozen=True)
class ComponentAlert:
    """One component's alert for this poll. `reason` is the same
    already-bounded, actionable text `ops_status_page.reason_for` mined from
    the component's own details -- never re-derived here, and `None` only
    for a `recovered` alert (a healthy component has no reason)."""

    component: str
    kind: str  # "entered" | "escalated" | "reminder" | "recovered"
    state: str
    previous_state: str | None
    reason: str | None
    age_seconds: int | None

    def render_line(self) -> str:
        if self.kind == "recovered":
            was = self.previous_state or "a bad state"
            return f"- {self.component}: recovered -> healthy (was {was})"
        verb = {"entered": "entered", "escalated": "escalated to", "reminder": "still in"}[self.kind]
        reason = self.reason or "no further detail available"
        return f"- {self.component}: {verb} {self.state} -- {reason}"

    def to_dict(self) -> dict:
        return {
            "component": self.component,
            "kind": self.kind,
            "state": self.state,
            "previous_state": self.previous_state,
            "reason": self.reason,
            "age_seconds": self.age_seconds,
        }


def evaluate_components(
    document: Mapping[str, object],
    state: AlertState,
    *,
    now: datetime,
    dedup_interval_seconds: float = DEFAULT_DEDUP_INTERVAL_SECONDS,
) -> tuple[list[ComponentAlert], dict[str, ComponentAlertState]]:
    """Compare one poll's combined document against persisted state, per the
    module docstring's `entered`/`escalated`/`reminder`/`recovered` rules.

    Pure with respect to `state`: returns the alerts plus a fresh
    `components` mapping rather than mutating the input, so a caller can
    inspect or discard the result before deciding to persist it.
    """

    reasons = document.get("reasons") or {}
    assert isinstance(reasons, dict)
    components_raw = document.get("components", [])
    assert isinstance(components_raw, list)
    updated: dict[str, ComponentAlertState] = dict(state.components)
    alerts: list[ComponentAlert] = []

    for component_doc in components_raw:
        assert isinstance(component_doc, dict)
        name = component_doc["component"]
        current = component_doc["state"]
        prior = state.components.get(name, ComponentAlertState())
        # `last_observed_state` alone is not enough: a poll that lands on
        # `unknown` deliberately preserves `last_alerted_state` while setting
        # `last_observed_state` to `"unknown"` (see the `unknown` branch
        # below), so an outstanding un-recovered alert must also count as
        # "was bad" even though the raw last-observed state no longer says so.
        was_bad = prior.last_observed_state in BAD_STATES or prior.last_alerted_state is not None
        is_bad = current in BAD_STATES
        reason = reasons.get(name)
        age_seconds = component_doc.get("age_seconds")

        alert: ComponentAlert | None = None
        if is_bad and not was_bad:
            alert = ComponentAlert(name, "entered", current, prior.last_observed_state, reason, age_seconds)
        elif current == "healthy" and was_bad:
            if prior.last_alerted_state is not None:
                alert = ComponentAlert(name, "recovered", current, prior.last_observed_state, reason, age_seconds)
        elif is_bad and was_bad:
            prior_severity = _BAD_STATE_SEVERITY.get(prior.last_alerted_state or "", 0)
            current_severity = _BAD_STATE_SEVERITY[current]
            if current_severity > prior_severity:
                alert = ComponentAlert(name, "escalated", current, prior.last_observed_state, reason, age_seconds)
            else:
                last_alert_at = _parse_iso(prior.last_alert_at)
                elapsed = (now - last_alert_at).total_seconds() if last_alert_at is not None else None
                if elapsed is None or elapsed >= dedup_interval_seconds:
                    alert = ComponentAlert(name, "reminder", current, prior.last_observed_state, reason, age_seconds)

        if is_bad:
            if alert is not None:
                updated[name] = ComponentAlertState(
                    last_observed_state=current, last_alerted_state=current, last_alert_at=_isoformat(now)
                )
            else:
                # Suppressed by dedup: last_observed_state advances, but the
                # alerted-state/timestamp pair is left as-is so the dedup
                # window keeps counting from the original alert, not this
                # poll.
                updated[name] = ComponentAlertState(
                    last_observed_state=current,
                    last_alerted_state=prior.last_alerted_state,
                    last_alert_at=prior.last_alert_at,
                )
        elif current == "healthy":
            # Always clears bad-state bookkeeping on reaching healthy, even
            # when this particular poll did not itself produce a
            # "recovered" alert (e.g. no prior alert existed to recover
            # from) -- a healthy component has nothing left to escalate or
            # remind about.
            updated[name] = ComponentAlertState(
                last_observed_state=current, last_alerted_state=None, last_alert_at=None
            )
        else:
            # unknown: bad-state bookkeeping (last_alerted_state/last_alert_at)
            # is left untouched because health cannot be determined at all, so
            # a later swing back to a real bad state is still judged against
            # the last thing actually alerted on, not silently reset.
            updated[name] = ComponentAlertState(
                last_observed_state=current,
                last_alerted_state=prior.last_alerted_state,
                last_alert_at=prior.last_alert_at,
            )

        if alert is not None:
            alerts.append(alert)

    return alerts, updated


@dataclass(frozen=True)
class MonitorSilenceAlert:
    """Raised when this alerting run itself appears to have stopped polling:
    `now` is more than `max_silence_seconds` past the previous run's
    persisted `last_run_at`."""

    last_run_at: datetime | None
    now: datetime
    elapsed_seconds: int
    max_silence_seconds: float

    def render_line(self) -> str:
        last = _isoformat(self.last_run_at) if self.last_run_at is not None else "never"
        return (
            f"- ops_alerts monitor: silent for {self.elapsed_seconds}s (last run {last}), "
            f"past the {int(self.max_silence_seconds)}s threshold -- "
            "check the scheduler (cron/systemd timer) invoking scripts/ops_alerts.py"
        )

    def to_dict(self) -> dict:
        return {
            "last_run_at": _isoformat(self.last_run_at) if self.last_run_at is not None else None,
            "elapsed_seconds": self.elapsed_seconds,
            "max_silence_seconds": self.max_silence_seconds,
        }


def check_monitor_silence(
    *,
    last_run_at: datetime | None,
    now: datetime,
    max_silence_seconds: float = DEFAULT_MONITOR_MAX_SILENCE_SECONDS,
) -> MonitorSilenceAlert | None:
    """`None` when there is no prior run to compare against (this is the very
    first poll ever, which is not silence) or the gap since it is within
    tolerance; a `MonitorSilenceAlert` once the gap exceeds
    `max_silence_seconds`. This is inherently self-resolving: the very fact
    that this check is running at all proves the scheduler has resumed, so
    no separate "recovered" event exists for monitor silence -- the single
    alert produced here, once, on the first poll after the gap, is the
    whole signal.
    """

    if last_run_at is None:
        return None
    elapsed = (now - last_run_at).total_seconds()
    if elapsed <= max_silence_seconds:
        return None
    return MonitorSilenceAlert(
        last_run_at=last_run_at, now=now, elapsed_seconds=round(elapsed), max_silence_seconds=max_silence_seconds
    )


@dataclass(frozen=True)
class Notification:
    """One poll's whole user-facing result: every component alert grouped
    together with any monitor-silence alert, as a single message rather than
    one per component."""

    generated_at: str
    component_alerts: tuple[ComponentAlert, ...]
    monitor_silence: MonitorSilenceAlert | None

    @property
    def is_empty(self) -> bool:
        return not self.component_alerts and self.monitor_silence is None

    def render_text(self) -> str:
        lines = [
            f"ops-alerts ALERT generated_at={self.generated_at} "
            f"component_alerts={len(self.component_alerts)} "
            f"monitor_silent={self.monitor_silence is not None}"
        ]
        if self.monitor_silence is not None:
            lines.append(self.monitor_silence.render_line())
        for alert in self.component_alerts:
            lines.append(alert.render_line())
        return "\n".join(lines) + "\n"

    def to_dict(self) -> dict:
        return {
            "generated_at": self.generated_at,
            "component_alerts": [alert.to_dict() for alert in self.component_alerts],
            "monitor_silence": self.monitor_silence.to_dict() if self.monitor_silence is not None else None,
        }


def build_notification(
    component_alerts: Sequence[ComponentAlert], monitor_silence: MonitorSilenceAlert | None, *, now: datetime
) -> Notification:
    return Notification(
        generated_at=_isoformat(now), component_alerts=tuple(component_alerts), monitor_silence=monitor_silence
    )


def _env_path(name: str, default: Path) -> Path:
    value = os.environ.get(name)
    return Path(value) if value else default


def _env_float(name: str, default: float) -> float:
    value = os.environ.get(name)
    if not value:
        return default
    try:
        return float(value)
    except ValueError:
        return default


def main(argv: Sequence[str] | None = None) -> int:
    """CLI entry point, meant to run on a short, fixed schedule (cron/systemd
    timer). Exit code: 0 on a quiet poll (no alert -- ordinary heartbeat
    traffic), 1 when a `Notification` was produced (see the module
    docstring's delivery-mechanism note)."""

    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument(
        "--repo",
        type=Path,
        default=_env_path(REPO_ENV_VAR, Path.cwd()),
        help="Local checkout for vps_loop/github facts",
    )
    parser.add_argument(
        "--github-repo",
        default=os.environ.get(GITHUB_REPO_ENV_VAR) or ops_status_page.DEFAULT_GITHUB_REPO_SLUG,
        help="owner/repo slug for the GitHub-relayed oracle_crawler/r2 heartbeat channels",
    )
    parser.add_argument("--state-path", type=Path, default=_env_path(STATE_PATH_ENV_VAR, DEFAULT_STATE_PATH))
    parser.add_argument(
        "--dedup-interval-seconds",
        type=float,
        default=_env_float(DEDUP_INTERVAL_ENV_VAR, DEFAULT_DEDUP_INTERVAL_SECONDS),
    )
    parser.add_argument(
        "--monitor-max-silence-seconds",
        type=float,
        default=_env_float(MONITOR_MAX_SILENCE_ENV_VAR, DEFAULT_MONITOR_MAX_SILENCE_SECONDS),
    )
    parser.add_argument("--format", choices=("text", "json"), default="text")
    args = parser.parse_args(argv)

    now = datetime.now(timezone.utc)
    state = load_state(args.state_path)

    monitor_silence = check_monitor_silence(
        last_run_at=_parse_iso(state.last_run_at), now=now, max_silence_seconds=args.monitor_max_silence_seconds
    )

    documents = ops_status_page.collect_all(local_repo=args.repo.resolve(), github_repo_slug=args.github_repo, now=now)
    document = ops_status_page.build_document(documents, now=now)

    component_alerts, updated_components = evaluate_components(
        document, state, now=now, dedup_interval_seconds=args.dedup_interval_seconds
    )
    save_state(args.state_path, AlertState(last_run_at=_isoformat(now), components=updated_components))

    notification = build_notification(component_alerts, monitor_silence, now=now)

    if notification.is_empty:
        if args.format == "json":
            print(json.dumps({"generated_at": notification.generated_at, "alert": False}))
        else:
            print(f"ops-alerts  generated_at={notification.generated_at}  no anomalies")
        return 0

    if args.format == "json":
        print(json.dumps(notification.to_dict(), indent=2))
    else:
        sys.stdout.write(notification.render_text())
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
