"""`python -m ml.cli <command>`: the workbench's jobs, run by its systemd timers."""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from collections.abc import Sequence
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from ml import sync


def _run(cmd: Sequence[str]) -> None:
    subprocess.run(list(cmd), check=True)


def _capture(cmd: Sequence[str]) -> str:
    return subprocess.run(list(cmd), check=True, capture_output=True, text=True).stdout


def _sync(args: argparse.Namespace) -> int:
    bucket, endpoint = os.environ["OBJECT_STORE_BUCKET"], os.environ["OBJECT_STORE_ENDPOINT"]
    done = sync.load_done(args.state)
    actions = sync.plan(sync.parse_keys(sync.list_keys(bucket, endpoint, _capture)), done)
    if args.dry_run:
        sys.stdout.writelines(f"{action}\n" for action in actions)
        return 0
    failures = sync.execute(
        actions,
        bucket=bucket,
        endpoint=endpoint,
        work_dir=args.work,
        state_path=args.state,
        done=done,
        today_jst=datetime.now(ZoneInfo("Asia/Tokyo")).date(),
        run=_run,
        python=sys.executable,
    )
    sys.stderr.writelines(f"{failure}\n" for failure in failures)
    return 1 if failures else 0


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="ml.cli")
    sub = parser.add_subparsers(dest="command", required=True)
    p_sync = sub.add_parser("sync", help="Mirror R2's archives into the replica")
    p_sync.add_argument("--state", type=Path, required=True)
    p_sync.add_argument("--work", type=Path, required=True)
    p_sync.add_argument("--dry-run", action="store_true")
    p_sync.set_defaults(handler=_sync)
    args = parser.parse_args(argv)
    return int(args.handler(args))


if __name__ == "__main__":
    raise SystemExit(main())
