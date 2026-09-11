#!/usr/bin/env python3
"""Pull the Oracle collector's published heartbeat onto the VPS.

`oracle_cloud/v3/bin/publish-status.sh` (cron, on Oracle) POSTs
`status-snapshot.sh`'s operations-status document (see `scripts/ops_status.py`
for the contract) to GitHub as a `repository_dispatch` event, authenticated
with a token that lives only on Oracle and is unrelated to any SSH key.
`.github/workflows/oracle-heartbeat-listener.yml` echoes it as a single
`ORACLE_STATUS <json>` line in its own run log -- the only place a
`repository_dispatch` payload survives after the triggering run completes.

This module is the VPS-side other half of that channel: it reads the latest
such line back out via the VPS's own, already-configured `gh` authentication
(the same one `/vps-loop-run` uses for its PR work -- nothing new is granted
here), validates it against the operations-status contract, and enforces
replay resistance with a small persisted watermark: a document whose
`observed_at` is not strictly newer than the last one this collector accepted
is rejected outright, so replaying an old, captured dispatch call can at best
reproduce a fact already on record -- it can never make stale data look fresh
again on a later read.

A channel failure (no `gh`, no run yet, a malformed log line, a document that
fails contract validation, or a rejected replay) always raises rather than
returning a fabricated status -- callers must treat that as `unknown`, per
this repo's "API failure produces unknown, never a false green status" rule
(see NEXT_TASK.md item 121), not silently reuse the last-known-good value as
if it were still current.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from scripts.ops_status import ComponentStatus, OpsStatusError, from_json_dict

WORKFLOW_FILE = "oracle-heartbeat-listener.yml"
DEFAULT_REPO = "TripAndCode/transit-app"
DEFAULT_CACHE_PATH = Path("/root/.oracle-status-watermark.json")

# The listener always logs the whole document as one compact-JSON line (see
# oracle-heartbeat-listener.yml); `re.DOTALL` is unnecessary since `jq -c`
# never emits an embedded newline. The captured text is handed to
# `json.loads` rather than shape-matched here, so a malformed or
# unexpectedly-shaped payload (not valid JSON, or valid JSON that isn't an
# object) surfaces as a specific, distinguishable error instead of silently
# not matching this pattern at all.
_LOG_LINE_RE = re.compile(r"^ORACLE_STATUS (.+)$", re.MULTILINE)


class OracleStatusUnavailable(Exception):
    """The channel itself could not be read: no `gh`, no run, bad log line, or an
    invalid document. Callers must treat this as `unknown`, never as a stale
    carried-over status."""


class OracleStatusReplayed(Exception):
    """The fetched document's `observed_at` is not strictly newer than the last
    one this collector accepted. Carries the parsed (but rejected) status so a
    caller can still log what was seen without treating it as newly current."""

    def __init__(self, message: str, status: ComponentStatus) -> None:
        super().__init__(message)
        self.status = status


def default_log_fetcher(repo: str) -> str:
    """Fetch `WORKFLOW_FILE`'s most recent run's combined log via `gh`.

    Two separate `gh` calls (list then view) rather than one, matching
    vps-heartbeat-watchdog.yml's own established pattern for reading this
    kind of heartbeat back out.
    """
    try:
        list_proc = subprocess.run(
            [
                "gh",
                "run",
                "list",
                "--repo",
                repo,
                "--workflow",
                WORKFLOW_FILE,
                "--json",
                "databaseId",
                "--limit",
                "1",
                "-q",
                ".[0].databaseId",
            ],
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise OracleStatusUnavailable(f"`gh run list` could not be executed: {exc}") from exc

    run_id = (list_proc.stdout or "").strip()
    # An empty result list makes the `-q` filter resolve to the literal
    # string "null", matching vps-heartbeat-watchdog.yml's own handling of
    # the identical `gh`/`jq` behavior.
    if list_proc.returncode != 0 or not run_id or run_id == "null":
        raise OracleStatusUnavailable(
            f"no {WORKFLOW_FILE} run found, or `gh run list` failed (exit {list_proc.returncode}): "
            f"{(list_proc.stderr or '').strip()}"
        )

    try:
        view_proc = subprocess.run(
            ["gh", "run", "view", run_id, "--repo", repo, "--log"],
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise OracleStatusUnavailable(f"`gh run view` could not be executed: {exc}") from exc

    if view_proc.returncode != 0:
        raise OracleStatusUnavailable(f"`gh run view {run_id} --log` failed: {(view_proc.stderr or '').strip()}")
    return view_proc.stdout


def parse_latest_status(log_text: str) -> dict:
    """Extract the last `ORACLE_STATUS <json>` line's payload from a run's log text."""
    matches = _LOG_LINE_RE.findall(log_text)
    if not matches:
        raise OracleStatusUnavailable(f"no ORACLE_STATUS line found in the {WORKFLOW_FILE} run's log")
    try:
        parsed = json.loads(matches[-1])
    except json.JSONDecodeError as exc:
        raise OracleStatusUnavailable(f"the latest ORACLE_STATUS line is not valid JSON: {exc}") from exc
    if not isinstance(parsed, dict):
        raise OracleStatusUnavailable("the latest ORACLE_STATUS line did not decode to a JSON object")
    return parsed


def _load_watermark(cache_path: Path) -> datetime | None:
    try:
        raw = json.loads(cache_path.read_text(encoding="utf-8"))
        return datetime.fromisoformat(str(raw["observed_at"]).replace("Z", "+00:00"))
    except (OSError, ValueError, KeyError, TypeError):
        return None


def _save_watermark(cache_path: Path, observed_at: datetime) -> None:
    tmp_path = cache_path.with_name(cache_path.name + ".tmp")
    tmp_path.write_text(
        json.dumps({"observed_at": observed_at.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")}),
        encoding="utf-8",
    )
    tmp_path.replace(cache_path)


def collect_oracle_status(
    *,
    repo: str = DEFAULT_REPO,
    cache_path: Path = DEFAULT_CACHE_PATH,
    log_fetcher: Callable[[str], str] = default_log_fetcher,
) -> ComponentStatus:
    """Fetch, validate, and replay-check the latest Oracle collector heartbeat.

    Raises `OracleStatusUnavailable` when the channel itself can't produce a
    contract-valid document, and `OracleStatusReplayed` when it can, but the
    document is not newer than the last one already accepted. Only on a
    clean return does it advance the persisted watermark -- a rejected
    document must never be mistaken for the new high-water mark on a later
    call.
    """
    raw = parse_latest_status(log_fetcher(repo))
    try:
        status = from_json_dict(raw)
    except OpsStatusError as exc:
        raise OracleStatusUnavailable(f"ORACLE_STATUS document failed contract validation: {exc}") from exc

    watermark = _load_watermark(cache_path)
    if watermark is not None and status.observed_at <= watermark:
        raise OracleStatusReplayed(
            f"fetched observed_at {status.observed_at.isoformat()} is not newer than "
            f"the last accepted {watermark.isoformat()}",
            status,
        )

    _save_watermark(cache_path, status.observed_at)
    return status


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", default=DEFAULT_REPO)
    parser.add_argument("--cache-path", type=Path, default=DEFAULT_CACHE_PATH)
    args = parser.parse_args(argv)

    try:
        status = collect_oracle_status(repo=args.repo, cache_path=args.cache_path)
    except OracleStatusReplayed as exc:
        print(f"REPLAYED: {exc}", file=sys.stderr)
        return 1
    except OracleStatusUnavailable as exc:
        print(f"UNAVAILABLE: {exc}", file=sys.stderr)
        return 2

    from scripts.ops_status import to_json_dict

    print(json.dumps(to_json_dict(status)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
