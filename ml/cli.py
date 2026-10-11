"""`python -m ml.cli <command>`: the workbench's jobs, run by its systemd timers."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from collections.abc import Sequence
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from clickhouse_connect.driver.client import Client

from ml import sync
from ml.adoption import judge
from ml.backtest import DataSpan, evaluate_agency, lookback_days, needs_older_cells, results_from_json, results_to_json
from ml.cells import Cell
from ml.data import agencies_with_data, count_days, date_span, fetch_cells
from ml.model_result import result_from_json, result_to_json
from ml.r2 import upload_dir, upload_file
from ml.report import render
from pipeline.clickhouse import get_client


def _today_jst() -> date:
    return datetime.now(ZoneInfo("Asia/Tokyo")).date()


def _run(cmd: Sequence[str]) -> None:
    subprocess.run(list(cmd), check=True)


def _capture(cmd: Sequence[str]) -> str:
    return subprocess.run(list(cmd), check=True, capture_output=True, text=True).stdout


def _use_store_credentials() -> None:
    # `sync` reads this same EnvironmentFile and must keep using the read-only
    # transit-archives pair in AWS_ACCESS_KEY_ID/AWS_SECRET_ACCESS_KEY -- so
    # `train`/`predict` swap in the read-write transit-ml pair only for their
    # own (separate, short-lived) process, from distinctly named variables.
    os.environ["AWS_ACCESS_KEY_ID"] = os.environ["ML_STORE_ACCESS_KEY_ID"]
    os.environ["AWS_SECRET_ACCESS_KEY"] = os.environ["ML_STORE_SECRET_ACCESS_KEY"]


def _sync(args: argparse.Namespace) -> int:
    bucket, endpoint = os.environ["OBJECT_STORE_BUCKET"], os.environ["OBJECT_STORE_ENDPOINT"]
    done = sync.load_done(args.state)
    actions = sync.plan(sync.parse_keys(sync.list_keys(bucket, endpoint, _capture)), done)
    if args.dry_run:
        sys.stdout.writelines(f"{action}\n" for action in actions)
        return 0
    notices: list[str] = []
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
        skip_static=frozenset(args.skip_static),
        notices=notices,
    )
    sys.stderr.writelines(f"{line}\n" for line in (*notices, *failures))
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


def _train(args: argparse.Namespace) -> int:
    verdict = judge(result_from_json(args.backtest.read_text()))
    if not verdict.adopted:
        sys.stderr.write(f"not adopted, keeping the previous model: {'; '.join(verdict.reasons)}\n")
        return 0

    # Imported here: they need the optional `ml` group, which sync, report,
    # and the adoption gate above do not.
    from ml.dataset import load_runs
    from ml.features import route_ids, with_route_ids
    from ml.model_store import save_model_set
    from ml.models import ModelParams, fit_calibrated
    from ml.training import training_frame

    client = get_client()
    runs = load_runs(client, args.agency or agencies_with_data(client))
    routes = route_ids(runs)
    runs = with_route_ids(runs, routes)
    cutoff = _today_jst() - timedelta(days=1)
    params = ModelParams(window_days=args.window)
    train = training_frame(runs, cutoff, window_days=params.window_days, half_life_days=params.half_life_days)
    if train.empty:
        sys.stderr.write("not enough history to train; keeping the previous model\n")
        return 0
    models = fit_calibrated(train, params, cutoff)
    version = cutoff.isoformat()
    args.out.parent.mkdir(parents=True, exist_ok=True)
    save_model_set(models, args.out, version=version, routes=routes)

    bucket, endpoint = os.environ["ML_STORE_BUCKET"], os.environ["ML_STORE_ENDPOINT"]
    _use_store_credentials()
    upload_dir(args.out, bucket, f"models/{version}", endpoint, run=_run)
    latest_path = args.out.parent / "latest.json"
    latest_path.write_text(json.dumps({"version": version, "window_days": params.window_days}))
    upload_file(latest_path, bucket, "models/latest.json", endpoint, run=_run)
    return 0


_CELL = ["agency_id", "route_code", "service_date", "hour"]


def _predict(args: argparse.Namespace) -> int:
    model_dir = args.model
    if not (model_dir / "meta.json").exists():
        sys.stderr.write(f"no published model at {model_dir}; run `ml train` first\n")
        return 1

    # Imported here: they need the optional `ml` group, which sync and
    # report do not.
    import pandas as pd

    from ml.dataset import load_runs
    from ml.features import build_frame, template_future_runs, with_route_ids
    from ml.model_backtest import MIN_MODEL_DAYS
    from ml.model_store import load_model_set, load_routes
    from ml.models import predict
    from ml.training import HORIZON_DAYS

    models = load_model_set(model_dir)
    routes = load_routes(model_dir)
    client = get_client()
    runs = with_route_ids(load_runs(client, args.agency or agencies_with_data(client)), routes)
    origin = _today_jst()
    # `runs` is ClickHouse's observed-trip history -- origin+1..+horizon_days
    # haven't happened, so build_frame's target slate is templated from the
    # most recent history sharing each future day's weekday (see
    # template_future_runs), not read from `runs` itself.
    template = template_future_runs(runs, origin, horizon_days=HORIZON_DAYS)
    frame = build_frame(
        pd.concat([runs, template], ignore_index=True), origin, window_days=args.window, horizon_days=HORIZON_DAYS
    )
    preds = predict(models, frame)

    # Below MIN_MODEL_DAYS of its own history, an agency's forecast is worse
    # than its own slot average (see ml.model_backtest's own fallback gate) --
    # never publish a model forecast for it; the app falls back to B0.
    seen = runs[runs["service_date"] <= pd.Timestamp(origin)].groupby("agency_id")["service_date"].nunique()
    on_model = {int(agency) for agency, days in seen.items() if days >= MIN_MODEL_DAYS}

    cells = frame[_CELL].assign(mean_min=preds.mean, q10=preds.q10, q50=preds.q50, q90=preds.q90)
    cells = cells[cells["agency_id"].isin(on_model)]
    # One run per trip, but one forecast per (route, date, hour) slot: several
    # trips can share an hour, and agg_route_forecast_ml's primary key is the
    # slot, not the trip.
    forecast = cells.groupby(_CELL, as_index=False, observed=True).agg(
        mean_min=("mean_min", "mean"), q10=("q10", "mean"), q50=("q50", "mean"), q90=("q90", "mean")
    )
    forecast["model_version"] = json.loads((model_dir / "meta.json").read_text())["version"]
    forecast["origin_date"] = origin.isoformat()
    forecast["generated_at"] = datetime.now(ZoneInfo("Asia/Tokyo")).isoformat()

    args.out.parent.mkdir(parents=True, exist_ok=True)
    forecast.to_parquet(args.out)
    manifest = args.out.parent / "manifest.json"
    manifest.write_text(json.dumps({"rows": len(forecast), "origin_date": origin.isoformat()}))

    bucket, endpoint = os.environ["ML_STORE_BUCKET"], os.environ["ML_STORE_ENDPOINT"]
    _use_store_credentials()
    prefix = f"forecasts/{origin.isoformat()}"
    upload_file(args.out, bucket, f"{prefix}/forecast.parquet", endpoint, run=_run)
    upload_file(manifest, bucket, f"{prefix}/manifest.json", endpoint, run=_run)
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
    p_sync.add_argument(
        "--skip-static",
        metavar="KEY",
        action="append",
        default=[],
        help="give up on this static archive (its bucket key); later days run under the previous timetable",
    )
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

    p_train_live = sub.add_parser("train", help="Fit and publish this week's live model, if adopted")
    p_train_live.add_argument("--backtest", type=Path, required=True)
    p_train_live.add_argument("--out", type=Path, required=True)
    p_train_live.add_argument("--window", type=int, default=28)
    p_train_live.add_argument("--agency", type=int, action="append")
    p_train_live.set_defaults(handler=_train)

    p_predict = sub.add_parser("predict", help="Predict the next 7 days with the latest published model")
    p_predict.add_argument("--model", type=Path, required=True)
    p_predict.add_argument("--out", type=Path, required=True)
    p_predict.add_argument("--window", type=int, default=28)
    p_predict.add_argument("--agency", type=int, action="append")
    p_predict.set_defaults(handler=_predict)

    args = parser.parse_args(argv)
    return int(args.handler(args))


if __name__ == "__main__":
    raise SystemExit(main())
