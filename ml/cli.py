"""`python -m ml.cli <command>`: the workbench's jobs, run by its systemd timers."""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from collections.abc import Sequence
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from ml import sync
from ml.backtest import DataSpan, evaluate_agency, lookback_days, results_from_json, results_to_json
from ml.data import agencies_with_data, count_days, date_span, fetch_cells
from ml.report import render
from pipeline.clickhouse import get_client


def _today_jst() -> date:
    return datetime.now(ZoneInfo("Asia/Tokyo")).date()


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
        today_jst=_today_jst(),
        run=_run,
        python=sys.executable,
    )
    sys.stderr.writelines(f"{failure}\n" for failure in failures)
    return 1 if failures else 0


def _backtest(args: argparse.Namespace) -> int:
    client = get_client()
    results = []
    for agency_id in args.agency or agencies_with_data(client):
        edges = date_span(client, agency_id)
        if edges is None:
            continue
        first, last = edges
        since = last - timedelta(days=lookback_days(args.origins, args.window))
        cells = fetch_cells(client, agency_id, since=since)
        if cells:
            span = DataSpan(first, last, count_days(client, agency_id))
            results.append(
                evaluate_agency(agency_id, cells, origin_count=args.origins, window_days=args.window, span=span)
            )
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(results_to_json(results))
    return 0


def _report(args: argparse.Namespace) -> int:
    results = results_from_json(args.input.read_text())
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(render(results, generated=_today_jst()))
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="ml.cli")
    sub = parser.add_subparsers(dest="command", required=True)

    p_sync = sub.add_parser("sync", help="Mirror R2's archives into the replica")
    p_sync.add_argument("--state", type=Path, required=True)
    p_sync.add_argument("--work", type=Path, required=True)
    p_sync.add_argument("--dry-run", action="store_true")
    p_sync.set_defaults(handler=_sync)

    p_backtest = sub.add_parser("backtest", help="Score the baselines with rolling origins")
    p_backtest.add_argument("--out", type=Path, required=True)
    p_backtest.add_argument("--origins", type=int, default=28)
    p_backtest.add_argument("--window", type=int, default=28)
    p_backtest.add_argument("--agency", type=int, action="append")
    p_backtest.set_defaults(handler=_backtest)

    p_report = sub.add_parser("report", help="Render a backtest as HTML")
    p_report.add_argument("--in", dest="input", type=Path, required=True)
    p_report.add_argument("--out", type=Path, required=True)
    p_report.set_defaults(handler=_report)

    args = parser.parse_args(argv)
    return int(args.handler(args))


if __name__ == "__main__":
    raise SystemExit(main())
