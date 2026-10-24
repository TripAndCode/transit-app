"""`python -m ml.cli <command>`: the workbench's jobs, run by its systemd timers."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import subprocess
import sys
from collections.abc import Sequence
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING
from zoneinfo import ZoneInfo

import asyncpg
from clickhouse_connect.driver.client import Client

from ml import sync
from ml.adoption import judge
from ml.backtest import DataSpan, evaluate_agency, lookback_days, needs_older_cells, results_from_json, results_to_json
from ml.cells import Cell
from ml.compare import FITS, compare, render_markdown, variant_name
from ml.data import agencies_with_data, count_days, date_span, fetch_cells
from ml.model_result import result_from_json, result_to_json
from ml.r2 import upload_dir, upload_file
from ml.report import render
from pipeline.clickhouse import get_client

if TYPE_CHECKING:
    from pipeline.day_types import DayTypes


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
    access_key, secret_key = os.environ["ML_STORE_ACCESS_KEY_ID"], os.environ["ML_STORE_SECRET_ACCESS_KEY"]
    # systemd's EnvironmentFile= sets an empty-but-present var for `VAR=` --
    # env.example ships exactly that, so a `KeyError` alone would not catch
    # an operator who never filled it in.
    if not access_key or not secret_key:
        raise SystemExit("ML_STORE_ACCESS_KEY_ID/ML_STORE_SECRET_ACCESS_KEY must be set, not empty")
    os.environ["AWS_ACCESS_KEY_ID"] = access_key
    os.environ["AWS_SECRET_ACCESS_KEY"] = secret_key


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


def _snapshot_runs(args: argparse.Namespace) -> int:
    from ml.dataset import load_runs, write_runs

    client = get_client()
    write_runs(load_runs(client, args.agency or agencies_with_data(client)), args.out)
    return 0


def _variant_eval(args: argparse.Namespace) -> int:
    """Writes one result per point, `<out-dir>/<variant>.json`, never touching
    the weekly job's own files unless pointed at them."""
    from dataclasses import replace

    from ml.dataset import read_runs
    from ml.model_backtest import run_backtests
    from ml.models import ModelParams

    params = replace(ModelParams(window_days=args.window), **FITS[args.fit])
    points = tuple(dict.fromkeys(args.point or ["mean"]))
    results = run_backtests(read_runs(args.runs), params=params, origin_count=args.origins, points=points)
    args.out_dir.mkdir(parents=True, exist_ok=True)
    for point, result in results.items():
        (args.out_dir / f"{variant_name(args.fit, point)}.json").write_text(result_to_json(result))
    return 0


_COMPARISON = "comparison"


def _compare(args: argparse.Namespace) -> int:
    results = {
        path.stem: result_from_json(path.read_text())
        for path in sorted(args.dir.glob("*.json"))
        if path.stem != _COMPARISON
    }
    comparison = compare(results, baseline=args.baseline)
    text = render_markdown(comparison)
    (args.dir / f"{_COMPARISON}.json").write_text(json.dumps(comparison, indent=1))
    (args.dir / f"{_COMPARISON}.md").write_text(text)
    sys.stdout.write(text)
    return 0


def _train(args: argparse.Namespace) -> int:
    verdict = judge(result_from_json(args.backtest.read_text()))
    if not verdict.adopted:
        sys.stderr.write(f"not adopted, keeping the previous model: {'; '.join(verdict.reasons)}\n")
        return 0

    # Read and swap credentials before the fit, so a misconfigured env fails
    # fast instead of after a full fit plus a local overwrite.
    bucket, endpoint = os.environ["ML_STORE_BUCKET"], os.environ["ML_STORE_ENDPOINT"]
    _use_store_credentials()

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
    train = training_frame(
        runs,
        cutoff,
        window_days=params.window_days,
        half_life_days=params.half_life_days,
        clip_quantile=params.clip_quantile,
    )
    if train.empty:
        sys.stderr.write("not enough history to train; keeping the previous model\n")
        return 0
    models = fit_calibrated(train, params, cutoff)
    version = cutoff.isoformat()
    save_model_set(
        models,
        args.out,
        version=version,
        routes=routes,
        window_days=params.window_days,
        point=params.point,
        clip_quantile=params.clip_quantile,
    )

    upload_dir(args.out, bucket, f"models/{version}", endpoint, run=_run)
    latest_path = args.out.parent / "latest.json"
    latest_path.write_text(json.dumps({"version": version, "window_days": params.window_days}))
    upload_file(latest_path, bucket, "models/latest.json", endpoint, run=_run)
    return 0


_CELL = ["agency_id", "route_code", "service_date", "hour"]


async def _read_day_types(conn, agency_ids: Sequence[int], first: date, last: date) -> dict[int, DayTypes]:
    """Each agency's day types over [first, last] from the replica's feed
    calendars. A replica whose migrations predate the calendar table gives
    none, so every agency falls back to the weekday rule; so does an agency
    whose calendar is empty, because its timetable loaded before the table
    existed and sync does not reload a timetable it already holds."""
    # Imported here so sync, which keeps the replica current, does not depend
    # on what only predict needs.
    from pipeline.day_types import load_day_types

    if await conn.fetchval("SELECT to_regclass('static_calendar')") is None:
        return {}
    return {agency_id: await load_day_types(conn, agency_id, first, last) for agency_id in agency_ids}


def _calendar_day_types(database_url: str, agency_ids: Sequence[int], first: date, last: date) -> dict[int, DayTypes]:
    async def read() -> dict[int, DayTypes]:
        conn = await asyncpg.connect(database_url)
        try:
            return await _read_day_types(conn, agency_ids, first, last)
        finally:
            await conn.close()

    return asyncio.run(read())


def _predict(args: argparse.Namespace) -> int:
    model_dir = args.model
    if not (model_dir / "meta.json").exists():
        sys.stderr.write(f"no published model at {model_dir}; run `ml train` first\n")
        return 1

    # Read and swap credentials before the heavy predict work, so a
    # misconfigured env fails fast instead of after it.
    bucket, endpoint = os.environ["ML_STORE_BUCKET"], os.environ["ML_STORE_ENDPOINT"]
    database_url = os.environ.get("DATABASE_URL")
    if not database_url:
        raise SystemExit("DATABASE_URL must be set, not empty: predict reads the feed calendars from the replica")
    _use_store_credentials()

    # Imported here: they need the optional `ml` group, which sync and
    # report do not.
    import pandas as pd

    from ml.dataset import load_runs
    from ml.features import build_frame, template_future_runs, template_span, with_route_ids
    from ml.model_backtest import MIN_MODEL_DAYS
    from ml.model_store import load_model_set, load_routes, read_meta
    from ml.models import point_forecast, predict
    from ml.training import HORIZON_DAYS

    models = load_model_set(model_dir)
    routes = load_routes(model_dir)
    meta = read_meta(model_dir)
    client = get_client()
    runs = with_route_ids(load_runs(client, args.agency or agencies_with_data(client)), routes)
    origin = _today_jst()
    # `runs` is ClickHouse's observed-trip history -- origin+1..+horizon_days
    # haven't happened, so build_frame's target slate is templated from the
    # most recent complete day of each future day's day type (see
    # template_future_runs), not read from `runs` itself. An agency whose feed
    # stopped gets no template, so nothing is published for it.
    first, last = template_span(origin, HORIZON_DAYS)
    agency_ids = sorted(int(agency) for agency in runs["agency_id"].unique())
    day_types = _calendar_day_types(database_url, agency_ids, first, last)
    template = template_future_runs(runs, origin, horizon_days=HORIZON_DAYS, day_types=day_types)
    # A meta with no clip_quantile or point belongs to a model fit on unclipped
    # features that shows its mean.
    frame = build_frame(
        pd.concat([runs, template], ignore_index=True),
        origin,
        window_days=meta["window_days"],
        horizon_days=HORIZON_DAYS,
        clip_quantile=meta.get("clip_quantile"),
    )
    preds = predict(models, frame)
    point = point_forecast(preds, meta.get("point", "mean"))

    # Below MIN_MODEL_DAYS of its own history, an agency's forecast is worse
    # than its own slot average (see ml.model_backtest's own fallback gate) --
    # never publish a model forecast for it; the app falls back to B0.
    seen = runs[runs["service_date"] < pd.Timestamp(origin)].groupby("agency_id")["service_date"].nunique()
    on_model = {int(agency) for agency, days in seen.items() if days >= MIN_MODEL_DAYS}

    cells = frame[_CELL].assign(mean_min=point, q10=preds.q10, q50=preds.q50, q90=preds.q90)
    cells = cells[cells["agency_id"].isin(on_model)]
    # One run per trip, but one forecast per (route, date, hour) slot: several
    # trips can share an hour, and agg_route_forecast_ml's primary key is the
    # slot, not the trip.
    forecast = cells.groupby(_CELL, as_index=False, observed=True).agg(
        mean_min=("mean_min", "mean"), q10=("q10", "mean"), q50=("q50", "mean"), q90=("q90", "mean")
    )
    forecast["model_version"] = meta["version"]
    forecast["origin_date"] = origin.isoformat()
    forecast["generated_at"] = datetime.now(ZoneInfo("Asia/Tokyo")).isoformat()

    args.out.parent.mkdir(parents=True, exist_ok=True)
    forecast.to_parquet(args.out)
    manifest = args.out.parent / "manifest.json"
    manifest.write_text(json.dumps({"rows": len(forecast), "origin_date": origin.isoformat()}))

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

    p_snapshot = sub.add_parser("snapshot-runs", help="Save the replica's runs for variant-eval")
    p_snapshot.add_argument("--out", type=Path, required=True)
    p_snapshot.add_argument("--agency", type=int, action="append")
    p_snapshot.set_defaults(handler=_snapshot_runs)

    p_variant = sub.add_parser("variant-eval", help="Backtest one model variant from a runs snapshot")
    p_variant.add_argument("--runs", type=Path, required=True)
    p_variant.add_argument("--fit", choices=sorted(FITS), required=True)
    p_variant.add_argument("--point", choices=["mean", "q50"], action="append")
    p_variant.add_argument("--out-dir", type=Path, required=True)
    p_variant.add_argument("--origins", type=int, default=28)
    p_variant.add_argument("--window", type=int, default=28)
    p_variant.set_defaults(handler=_variant_eval)

    p_compare = sub.add_parser("compare", help="Compare the variant backtests in a directory")
    p_compare.add_argument("--dir", type=Path, required=True)
    p_compare.add_argument("--baseline", default="base")
    p_compare.set_defaults(handler=_compare)

    p_train_live = sub.add_parser("train", help="Fit and publish this week's live model, if adopted")
    p_train_live.add_argument("--backtest", type=Path, required=True)
    p_train_live.add_argument("--out", type=Path, required=True)
    p_train_live.add_argument("--window", type=int, default=28)
    p_train_live.add_argument("--agency", type=int, action="append")
    p_train_live.set_defaults(handler=_train)

    p_predict = sub.add_parser("predict", help="Predict the next 7 days with the latest published model")
    p_predict.add_argument("--model", type=Path, required=True)
    p_predict.add_argument("--out", type=Path, required=True)
    p_predict.add_argument("--agency", type=int, action="append")
    p_predict.set_defaults(handler=_predict)

    args = parser.parse_args(argv)
    return int(args.handler(args))


if __name__ == "__main__":
    raise SystemExit(main())
