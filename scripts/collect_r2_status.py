#!/usr/bin/env python3
"""Pull the Oracle storage-metrics collector's published heartbeat onto the VPS.

`oracle_cloud/v3/bin/storage-metrics.sh` (cron, on Oracle) builds one
operations-status document (component "r2"; see `scripts/ops_status.py` for
the contract) reporting local filesystem usage and R2 object count/bytes
split by rt/ and static/. The same `bin/publish-status.sh` already used for
the oracle_crawler heartbeat POSTs it to GitHub as a `repository_dispatch`
event -- pointed at storage-metrics.sh's own output file and a distinct event
type via `ORACLE_STATUS_FILE`/`ORACLE_STATUS_EVENT_TYPE`, not a second
publisher script. `.github/workflows/r2-storage-heartbeat-listener.yml`
echoes it as a single `R2_STORAGE_STATUS <json>` line in its own run log --
the only place a `repository_dispatch` payload survives after the triggering
run completes.

This module is the VPS-side other half of that channel, structurally
identical to `scripts/collect_oracle_status.py` (see that module for the
shared rationale on replay resistance and "a channel failure must produce
`unknown`, never a fabricated status"): it reads the latest such line back
out via the VPS's own already-configured `gh` authentication, validates it
against the operations-status contract, and enforces replay resistance with
its own persisted watermark, independent of the oracle_crawler channel's.
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

from scripts.ops_status import ComponentStatus, OpsStatusError, from_json_dict, to_json_dict

WORKFLOW_FILE = "r2-storage-heartbeat-listener.yml"
DEFAULT_REPO = "TripAndCode/transit-app"
DEFAULT_CACHE_PATH = Path("/root/.r2-status-watermark.json")

# `gh run view --log` prefixes every line with `<job>\t<step>\t<timestamp> `,
# so the marker is a substring, never the true start of a line -- this
# pattern is deliberately unanchored to find it anywhere in the line.
_LOG_LINE_RE = re.compile(r"R2_STORAGE_STATUS (.+)$", re.MULTILINE)


class R2StatusUnavailable(Exception):
    """The channel itself could not be read: no `gh`, no run, bad log line, or an
    invalid document. Callers must treat this as `unknown`, never as a stale
    carried-over status."""


class R2StatusReplayed(Exception):
    """The fetched document's `observed_at` is not strictly newer than the last
    one this collector accepted. Carries the parsed (but rejected) status so a
    caller can still log what was seen without treating it as newly current."""

    def __init__(self, message: str, status: ComponentStatus) -> None:
        super().__init__(message)
        self.status = status


def default_log_fetcher(repo: str) -> str:
    """Fetch `WORKFLOW_FILE`'s most recent run's combined log via `gh`.

    Two separate `gh` calls (list then view) rather than one, matching
    `scripts/collect_oracle_status.py`'s own identical pattern.
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
        raise R2StatusUnavailable(f"`gh run list` could not be executed: {exc}") from exc

    run_id = (list_proc.stdout or "").strip()
    # An empty result list makes the `-q` filter resolve to the literal
    # string "null", matching vps-heartbeat-watchdog.yml's own documented
    # handling of this exact `gh`/`jq` quirk.
    if list_proc.returncode != 0 or not run_id or run_id == "null":
        raise R2StatusUnavailable(
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
        raise R2StatusUnavailable(f"`gh run view` could not be executed: {exc}") from exc

    if view_proc.returncode != 0:
        raise R2StatusUnavailable(f"`gh run view {run_id} --log` failed: {(view_proc.stderr or '').strip()}")
    return view_proc.stdout


def parse_latest_status(log_text: str) -> dict:
    """Extract the last `R2_STORAGE_STATUS <json>` line's payload from a run's log text."""
    matches = _LOG_LINE_RE.findall(log_text)
    if not matches:
        raise R2StatusUnavailable(f"no R2_STORAGE_STATUS line found in the {WORKFLOW_FILE} run's log")
    try:
        parsed = json.loads(matches[-1])
    except json.JSONDecodeError as exc:
        raise R2StatusUnavailable(f"the latest R2_STORAGE_STATUS line is not valid JSON: {exc}") from exc
    if not isinstance(parsed, dict):
        raise R2StatusUnavailable("the latest R2_STORAGE_STATUS line did not decode to a JSON object")
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


def collect_r2_status(
    *,
    repo: str = DEFAULT_REPO,
    cache_path: Path = DEFAULT_CACHE_PATH,
    log_fetcher: Callable[[str], str] = default_log_fetcher,
) -> ComponentStatus:
    """Fetch, validate, and replay-check the latest R2 storage-metrics heartbeat.

    Raises `R2StatusUnavailable` when the channel itself can't produce a
    contract-valid document, and `R2StatusReplayed` when it can, but the
    document is not newer than the last one already accepted. Only on a
    clean return does it advance the persisted watermark -- a rejected
    document must never be mistaken for the new high-water mark on a later
    call.
    """
    raw = parse_latest_status(log_fetcher(repo))
    try:
        status = from_json_dict(raw)
    except OpsStatusError as exc:
        raise R2StatusUnavailable(f"R2_STORAGE_STATUS document failed contract validation: {exc}") from exc
    if status.component != "r2":
        raise R2StatusUnavailable(f"R2_STORAGE_STATUS document reports component {status.component!r}, expected 'r2'")

    watermark = _load_watermark(cache_path)
    if watermark is not None and status.observed_at <= watermark:
        raise R2StatusReplayed(
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
        status = collect_r2_status(repo=args.repo, cache_path=args.cache_path)
    except R2StatusReplayed as exc:
        print(f"REPLAYED: {exc}", file=sys.stderr)
        return 1
    except R2StatusUnavailable as exc:
        print(f"UNAVAILABLE: {exc}", file=sys.stderr)
        return 2

    print(json.dumps(to_json_dict(status)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
