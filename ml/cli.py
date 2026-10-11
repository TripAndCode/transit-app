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

from clickhouse_connect.driver.client import Client

from ml import sync
from ml.backtest import DataSpan, evaluate_agency, lookback_days, needs_older_cells, results_from_json, results_to_json
from ml.cells import Cell
from ml.data import agencies_with_data, count_days, date_span, fetch_cells
from ml.model_result import result_from_json, result_to_json
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


def _recent_cells(client: Client, agency_id: int, first: date, last: date, origins: int, window: int) -> list[Cell]:
    """The cells the latest origins score from, widening the lookback while
    gaps in the data push those origins' windows back past what was fetched."""
    lookback = lookback_days(origins, window)
    while True:
        since = last - timedelta(days=lookback)
        cells = fetch_cells(client, agency_id, since=since)
        if since <= first or not needs_older_cells(cells, since, origins, window):
            return cells
        lookback *= 2


def _backtest(args: argparse.Namespace) -> int:
    client = get_client()
    results = []
    for agency_id in args.agency or agencies_with_data(client):
        edges = date_span(client, agency_id)
        if edges is None:
            continue
        first, last = edges
        cells = _recent_cells(client, agency_id, first, last, args.origins, args.window)
        if cells:
            span = DataSpan(first, last, count_days(client, agency_id))
            results.append(
                evaluate_agency(agency_id, cells, origin_count=args.origins, window_days=args.window, span=span)
            )
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(results_to_json(results))
    return 0


def _train_eval(args: argparse.Namespace) -> int:
    # Imported here: they need the optional `ml` group, which sync and report do not.
    from ml.dataset import load_runs
    from ml.model_backtest import run_backtest
    from ml.models import ModelParams

    client = get_client()
    runs = load_runs(client, args.agency or agencies_with_data(client))
    result = run_backtest(runs, params=ModelParams(window_days=args.window), origin_count=args.origins)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(result_to_json(result))
    return 0


def _report(args: argparse.Namespace) -> int:
    results = results_from_json(args.input.read_text())
    models = result_from_json(args.models.read_text()) if args.models else None
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(render(results, generated=_today_jst(), models=models))
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
    p_report.add_argument("--models", type=Path)
    p_report.set_defaults(handler=_report)

    p_train = sub.add_parser("train-eval", help="Backtest the models against B0")
    p_train.add_argument("--out", type=Path, required=True)
    p_train.add_argument("--origins", type=int, default=28)
    p_train.add_argument("--window", type=int, default=28)
    p_train.add_argument("--agency", type=int, action="append")
    p_train.set_defaults(handler=_train_eval)

    args = parser.parse_args(argv)
    return int(args.handler(args))


if __name__ == "__main__":
    raise SystemExit(main())
