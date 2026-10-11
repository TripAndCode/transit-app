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

from ml import sync
from ml.backtest import evaluate_agency, results_from_json, results_to_json
from ml.data import agencies_with_data, fetch_cells
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


def _backtest(args: argparse.Namespace) -> int:
    client = get_client()
    results = []
    for agency_id in args.agency or agencies_with_data(client):
        cells = fetch_cells(client, agency_id)
        if cells:
            results.append(evaluate_agency(agency_id, cells, origin_count=args.origins, window_days=args.window))
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
    import os
    import sys

    from ml.adoption import judge
    from ml.model_result import result_from_json

    verdict = judge(result_from_json(args.backtest.read_text()))
    if not verdict.adopted:
        sys.stderr.write(f"not adopted, keeping the previous model: {'; '.join(verdict.reasons)}\n")
        return 0

    # Imported here: they need the optional `ml` group, which sync and
    # report do not.
    from ml.data import agencies_with_data
    from ml.dataset import load_runs
    from ml.model_store import save_model_set
    from ml.models import ModelParams, fit_calibrated
    from ml.r2 import upload_dir, upload_file
    from ml.training import training_frame

    client = get_client()
    runs = load_runs(client, args.agency or agencies_with_data(client))
    cutoff = _today_jst() - timedelta(days=1)
    params = ModelParams(window_days=args.window)
    train = training_frame(runs, cutoff, window_days=params.window_days, half_life_days=params.half_life_days)
    if train.empty:
        sys.stderr.write("not enough history to train; keeping the previous model\n")
        return 0
    models = fit_calibrated(train, params, cutoff)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    save_model_set(models, args.out)
    # save_model_set already wrote meta.json with interval_shift; add the
    # version here rather than deriving it from the local directory name
    # (which is a fixed path, e.g. /var/lib/transit-ml/model, overwritten
    # every week -- the version has to live inside the file, not the path).
    version = cutoff.isoformat()
    meta_path = args.out / "meta.json"
    meta = json.loads(meta_path.read_text())
    meta["version"] = version
    meta_path.write_text(json.dumps(meta))

    bucket, endpoint = os.environ["ML_STORE_BUCKET"], os.environ["ML_STORE_ENDPOINT"]
    upload_dir(args.out, bucket, f"models/{version}", endpoint, run=_run)
    latest_path = args.out.parent / "latest.json"
    latest_path.write_text(json.dumps({"version": version, "window_days": params.window_days}))
    upload_file(latest_path, bucket, "models/latest.json", endpoint, run=_run)
    return 0


def _predict(args: argparse.Namespace) -> int:
    import os
    import sys

    model_dir = args.model
    if not (model_dir / "meta.json").exists():
        sys.stderr.write(f"no published model at {model_dir}; run `ml train` first\n")
        return 1

    # Imported here: they need the optional `ml` group, which sync and
    # report do not.
    from ml.data import agencies_with_data
    from ml.dataset import load_runs
    from ml.features import build_frame
    from ml.model_store import load_model_set
    from ml.models import predict
    from ml.r2 import upload_file
    from ml.training import HORIZON_DAYS

    models = load_model_set(model_dir)
    client = get_client()
    runs = load_runs(client, args.agency or agencies_with_data(client))
    origin = _today_jst()
    frame = build_frame(runs, origin, window_days=args.window, horizon_days=HORIZON_DAYS)
    preds = predict(models, frame)
    forecast = frame[["agency_id", "route_code", "service_date", "hour"]].copy()
    forecast["mean_min"] = preds.mean
    forecast["q10"] = preds.q10
    forecast["q50"] = preds.q50
    forecast["q90"] = preds.q90
    forecast["model_version"] = json.loads((model_dir / "meta.json").read_text())["version"]
    forecast["origin_date"] = origin.isoformat()
    forecast["generated_at"] = datetime.now(ZoneInfo("Asia/Tokyo")).isoformat()

    args.out.parent.mkdir(parents=True, exist_ok=True)
    forecast.to_parquet(args.out)
    manifest = args.out.parent / "manifest.json"
    manifest.write_text(json.dumps({"rows": len(forecast), "origin_date": origin.isoformat()}))

    bucket, endpoint = os.environ["ML_STORE_BUCKET"], os.environ["ML_STORE_ENDPOINT"]
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
