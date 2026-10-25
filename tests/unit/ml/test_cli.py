import json
import os
import subprocess
from datetime import date, timedelta
from pathlib import Path

import pytest

from tests.unit.ml.ml_group import require

pd = require("pandas")
np = require("numpy")
require("lightgbm")
require("pyarrow")

from ml import cli  # noqa: E402
from ml.backtest import evaluate_agency, results_to_json  # noqa: E402
from ml.cells import Cell  # noqa: E402
from ml.cli import main  # noqa: E402

START = date(2026, 1, 1)
DAYS = 70  # >= ml.model_backtest.MIN_MODEL_DAYS, so the fixture agency is "on model"


def _runs():
    rng = np.random.default_rng(1)
    rows = [
        {
            "agency_id": 8,
            "route_code": route,
            "trip_id": f"{route}{trip}",
            "service_date": pd.Timestamp(START + timedelta(days=d)),
            "hour": 8 + trip,
            "service": "wk",
            "delay_min": base + rng.normal(0, 0.3),
            "stops": 10,
            "span_min": 30.0,
        }
        for d in range(DAYS)
        for route, base in (("LATE", 5.0), ("EARLY", 1.0))
        for trip in range(3)
    ]
    return pd.DataFrame(rows).astype(
        {
            "agency_id": "int16",
            "route_code": "string",
            "trip_id": "string",
            "hour": "int16",
            "service": "string",
            "delay_min": "float32",
            "stops": "int16",
            "span_min": "float32",
        }
    )


def test_unknown_commands_are_refused():
    with pytest.raises(SystemExit):
        main(["bogus"])


def test_report_reads_a_backtest_and_writes_html(tmp_path):
    cells = [Cell("R1", date(2026, 6, 1) + timedelta(days=d), 8, 1, 2.0) for d in range(40)]
    source = tmp_path / "backtest.json"
    source.write_text(results_to_json([evaluate_agency(8, cells, origin_count=3)]))
    out = tmp_path / "report" / "index.html"
    assert main(["report", "--in", str(source), "--out", str(out)]) == 0
    assert out.read_text().startswith("<!doctype html>")


def test_the_lookback_widens_while_gaps_push_the_origins_past_it(monkeypatch):
    first, last = date(2026, 1, 1), date(2026, 6, 30)
    # A long outage leaves only a few recent days, so the latest origins
    # have to reach back across it for their windows.
    recent = [last - timedelta(days=d) for d in range(5)]
    old = [date(2026, 3, 1) + timedelta(days=d) for d in range(60)]
    all_days = old + recent
    fetched_since: list[date] = []

    def fake_fetch(client, agency_id, *, since):
        fetched_since.append(since)
        return [Cell("R1", d, 8, 1, 2.0) for d in all_days if d >= since]

    monkeypatch.setattr(cli, "fetch_cells", fake_fetch)
    cells = cli._recent_cells(None, 8, first, last, origins=10, window=28)

    assert len(fetched_since) > 1
    assert fetched_since == sorted(fetched_since, reverse=True)
    assert len(cells) < len(all_days)
    full = [Cell("R1", d, 8, 1, 2.0) for d in all_days]
    scored = evaluate_agency(8, cells, origin_count=10, window_days=28)
    reference = evaluate_agency(8, full, origin_count=10, window_days=28)
    assert (scored.origins, scored.rows) == (reference.origins, reference.rows)


def test_report_renders_the_model_section_when_given_a_model_result(tmp_path):
    from datetime import date, timedelta

    from ml.backtest import evaluate_agency, results_to_json
    from ml.cells import Cell
    from ml.model_result import result_to_json
    from tests.unit.ml.test_report import _model_result

    source = tmp_path / "backtest.json"
    cells = [Cell("R1", date(2026, 6, 1) + timedelta(days=d), 8, 1, 2.0) for d in range(40)]
    source.write_text(results_to_json([evaluate_agency(8, cells, origin_count=3)]))
    models = tmp_path / "models.json"
    models.write_text(result_to_json(_model_result()))
    out = tmp_path / "index.html"
    assert main(["report", "--in", str(source), "--models", str(models), "--out", str(out)]) == 0
    assert "Model against B0" in out.read_text()


def test_train_eval_is_a_command():
    with pytest.raises(SystemExit) as exit_info:
        main(["train-eval", "--help"])
    assert exit_info.value.code == 0


def test_train_skips_publishing_when_the_backtest_was_not_adopted(tmp_path, monkeypatch):
    from ml.backtest import AgencyResult
    from ml.model_result import ModelBacktestResult, result_to_json

    backtest = tmp_path / "models.json"
    # One agency, no slice rows: skill_vs_b0/coverage come back None
    # (unmeasured), so judge() rejects on those two criteria -- the cheapest
    # fixture reliably rejected without a real skill/coverage computation.
    backtest.write_text(
        result_to_json(
            ModelBacktestResult(
                params={},
                origins=[date(2026, 6, 1)],
                cutoffs=[date(2026, 5, 31)],
                agencies=[AgencyResult(8, date(2026, 6, 1), date(2026, 6, 1), 1, [date(2026, 6, 1)], False, [])],
                intervals={},
                origin_skill=[],
            )
        )
    )
    out = tmp_path / "model"
    monkeypatch.setenv("ML_STORE_BUCKET", "transit-ml")
    monkeypatch.setenv("ML_STORE_ENDPOINT", "https://example.r2")
    assert main(["train", "--backtest", str(backtest), "--out", str(out)]) == 0
    assert not out.exists()


def test_train_is_a_command():
    with pytest.raises(SystemExit) as exit_info:
        main(["train", "--help"])
    assert exit_info.value.code == 0


def test_predict_without_a_published_model_fails_loudly(tmp_path, monkeypatch):
    monkeypatch.setenv("ML_STORE_BUCKET", "transit-ml")
    monkeypatch.setenv("ML_STORE_ENDPOINT", "https://example.r2")
    out = tmp_path / "forecast.parquet"
    assert main(["predict", "--model", str(tmp_path / "no-model-here"), "--out", str(out)]) != 0
    assert not out.exists()


def test_predict_is_a_command():
    with pytest.raises(SystemExit) as exit_info:
        main(["predict", "--help"])
    assert exit_info.value.code == 0


def _adopted_train(tmp_path, monkeypatch, run):
    """`train`'s judge, stores and clock stubbed so it fits and publishes,
    with `run` standing in for the aws CLI. `--out` sits in a directory of
    its own, beside nothing but what `train` writes. Returns the CLI args,
    `--out` and the version the fit publishes."""
    from dataclasses import asdict

    from ml.adoption import Verdict
    from ml.backtest import AgencyResult
    from ml.model_result import ModelBacktestResult, result_to_json
    from ml.models import ModelParams

    backtest = tmp_path / "models.json"
    backtest.write_text(
        result_to_json(
            ModelBacktestResult(
                params=asdict(ModelParams(rounds=40, num_leaves=7, min_data_in_leaf=5)),
                origins=[date(2026, 6, 1)],
                cutoffs=[date(2026, 5, 31)],
                agencies=[AgencyResult(8, date(2026, 6, 1), date(2026, 6, 1), 1, [date(2026, 6, 1)], False, [])],
                intervals={},
                origin_skill=[],
            )
        )
    )
    monkeypatch.setattr("ml.cli.judge", lambda result: Verdict(adopted=True, reasons=()))
    monkeypatch.setattr("ml.dataset.load_runs", lambda client, agency_ids: _runs())
    monkeypatch.setattr("ml.cli.get_client", lambda: object())
    monkeypatch.setattr("ml.cli._today_jst", lambda: START + timedelta(days=DAYS + 1))
    monkeypatch.setattr("ml.cli._run", run)
    monkeypatch.setenv("ML_STORE_BUCKET", "transit-ml")
    monkeypatch.setenv("ML_STORE_ENDPOINT", "https://example.r2")
    monkeypatch.setenv("ML_STORE_ACCESS_KEY_ID", "store-key")
    monkeypatch.setenv("ML_STORE_SECRET_ACCESS_KEY", "store-secret")
    # Pre-registered so monkeypatch restores them: _use_store_credentials
    # writes os.environ directly (it must, to reach the real aws CLI), and
    # monkeypatch only undoes a variable it itself set.
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "unused")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "unused")

    out = tmp_path / "ml" / "model"
    args = ["train", "--backtest", str(backtest), "--out", str(out), "--agency", "8"]
    return args, out, (START + timedelta(days=DAYS)).isoformat()


def _previously_published(out):
    """A stand-in for the model and pointer an earlier `train` published."""
    out.mkdir(parents=True)
    (out / "meta.json").write_text(json.dumps({"version": "previous"}))
    (out / "q0.5.txt").write_text("previous booster")
    (out.parent / "latest.json").write_text(json.dumps({"version": "previous"}))


def _tree(root):
    """Each path under `root`: a file's bytes, None for a directory."""
    return {str(path.relative_to(root)): path.read_bytes() if path.is_file() else None for path in root.rglob("*")}


def test_train_fits_and_publishes_the_model_when_adopted(tmp_path, monkeypatch):
    seen = []

    def run(cmd):
        # The served model is still the previous one while either upload runs.
        source = Path(cmd[3])
        uploaded = source / "meta.json" if "--recursive" in cmd else source
        seen.append((json.loads((out / "meta.json").read_text())["version"], json.loads(uploaded.read_text())))

    args, out, version = _adopted_train(tmp_path, monkeypatch, run)
    _previously_published(out)
    assert main(args) == 0

    assert [served for served, _ in seen] == ["previous", "previous"]
    assert [uploaded["version"] for _, uploaded in seen] == [version, version]
    meta = json.loads((out / "meta.json").read_text())
    assert meta["version"] == version
    # A median-point model never needs, so never publishes, a mean booster.
    assert meta["point"] == "q50" and not (out / "mean.txt").exists() and (out / "q0.5.txt").exists()
    assert (out / "routes.parquet").exists()
    assert json.loads((out.parent / "latest.json").read_text())["version"] == version
    assert sorted(path.name for path in out.parent.iterdir()) == ["latest.json", "model"]


@pytest.mark.parametrize(
    "fails",
    [lambda cmd: "--recursive" in cmd, lambda cmd: cmd[4] == "s3://transit-ml/models/latest.json"],
    ids=["model upload fails", "latest.json upload fails"],
)
def test_a_failed_upload_leaves_the_previously_published_model_served(tmp_path, monkeypatch, fails):
    def run(cmd):
        if fails(cmd):
            raise subprocess.CalledProcessError(1, cmd)

    args, out, _ = _adopted_train(tmp_path, monkeypatch, run)
    _previously_published(out)
    before = _tree(out.parent)

    with pytest.raises(subprocess.CalledProcessError):
        main(args)

    assert _tree(out.parent) == before


def _publish_model(raw, model_dir, point="q50"):
    from ml.features import route_ids, with_route_ids
    from ml.model_store import replace_dir, stage_model_set
    from ml.models import ModelParams, fit_calibrated
    from ml.training import training_frame

    routes = route_ids(raw)
    runs = with_route_ids(raw, routes)
    cutoff = START + timedelta(days=DAYS - 1)
    train = training_frame(runs, cutoff, window_days=28, half_life_days=28)
    params = ModelParams(rounds=40, num_leaves=7, min_data_in_leaf=5, point=point)
    models = fit_calibrated(train, params, cutoff)
    staged = stage_model_set(
        models,
        model_dir,
        version=cutoff.isoformat(),
        routes=routes,
        window_days=28,
        point=point,
        clip_quantile=None,
    )
    replace_dir(staged, model_dir)


def _stub_predict(monkeypatch, raw, origin):
    """Predict's stores stubbed out: `raw` as the replica's runs, no feed
    calendar, `origin` as today. Returns the upload calls."""
    monkeypatch.setattr("ml.dataset.load_runs", lambda client, agency_ids: raw)
    monkeypatch.setattr("ml.cli.get_client", lambda: object())
    monkeypatch.setattr("ml.cli._calendar_day_types", lambda database_url, agency_ids, first, last: {})
    monkeypatch.setattr("ml.cli._today_jst", lambda: origin)
    calls = []
    monkeypatch.setattr("ml.cli._run", calls.append)
    monkeypatch.setenv("ML_STORE_BUCKET", "transit-ml")
    monkeypatch.setenv("ML_STORE_ENDPOINT", "https://example.r2")
    monkeypatch.setenv("ML_STORE_ACCESS_KEY_ID", "store-key")
    monkeypatch.setenv("ML_STORE_SECRET_ACCESS_KEY", "store-secret")
    monkeypatch.setenv("DATABASE_URL", "postgresql://unused")
    # Pre-registered so monkeypatch restores them: _use_store_credentials
    # writes os.environ directly (it must, to reach the real aws CLI), and
    # monkeypatch only undoes a variable it itself set.
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "unused")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "unused")
    return calls


def test_predict_writes_one_row_per_future_cell_for_an_agency_with_enough_history(tmp_path, monkeypatch):
    raw = _runs()
    model_dir = tmp_path / "model"
    _publish_model(raw, model_dir)
    origin = START + timedelta(days=DAYS)
    calls = _stub_predict(monkeypatch, raw, origin)

    out = tmp_path / "forecast.parquet"
    assert main(["predict", "--model", str(model_dir), "--out", str(out), "--agency", "8"]) == 0

    forecast = pd.read_parquet(out)
    assert len(forecast) > 0
    assert not forecast.duplicated(["agency_id", "route_code", "service_date", "hour"]).any()
    assert forecast["service_date"].min() > pd.Timestamp(origin)
    assert forecast["service_date"].max() <= pd.Timestamp(origin) + pd.Timedelta(days=7)
    assert len(calls) == 2  # upload_file (the parquet) + upload_file (manifest.json)


def test_predict_publishes_nothing_for_an_agency_below_the_model_history_floor(tmp_path, monkeypatch):
    raw = _runs()[lambda f: f["service_date"] >= pd.Timestamp(START + timedelta(days=DAYS - 30))]
    model_dir = tmp_path / "model"
    _publish_model(raw, model_dir)
    _stub_predict(monkeypatch, raw, START + timedelta(days=DAYS))

    out = tmp_path / "forecast.parquet"
    assert main(["predict", "--model", str(model_dir), "--out", str(out), "--agency", "8"]) == 0
    assert pd.read_parquet(out).empty


def test_predict_publishes_nothing_for_an_agency_whose_data_stopped(tmp_path, monkeypatch):
    # Both agencies have the model's history, but agency 9's newest day is
    # four days before the origin.
    current = _runs()
    stopped = _runs().assign(agency_id=9).astype({"agency_id": "int16"})
    stopped = stopped[stopped["service_date"] <= pd.Timestamp(START + timedelta(days=DAYS - 4))]
    raw = pd.concat([current, stopped], ignore_index=True)
    model_dir = tmp_path / "model"
    _publish_model(raw, model_dir)
    _stub_predict(monkeypatch, raw, START + timedelta(days=DAYS))

    out = tmp_path / "forecast.parquet"
    assert main(["predict", "--model", str(model_dir), "--out", str(out), "--agency", "8", "--agency", "9"]) == 0
    assert set(pd.read_parquet(out)["agency_id"]) == {8}


@pytest.mark.parametrize("database_url", [None, ""])
def test_predict_without_a_database_url_fails_before_reading_the_replica(tmp_path, monkeypatch, database_url):
    # Unset, or present but empty, as systemd's EnvironmentFile= sets a bare
    # `DATABASE_URL=`. env.example ships a placeholder URL to fill in.
    model_dir = tmp_path / "model"
    model_dir.mkdir()
    (model_dir / "meta.json").write_text("{}")
    _stub_predict(monkeypatch, _runs(), START + timedelta(days=DAYS))
    loads = []
    monkeypatch.setattr("ml.dataset.load_runs", lambda client, agency_ids: loads.append(agency_ids))
    if database_url is None:
        monkeypatch.delenv("DATABASE_URL")
    else:
        monkeypatch.setenv("DATABASE_URL", database_url)

    with pytest.raises(SystemExit, match="DATABASE_URL"):
        main(["predict", "--model", str(model_dir), "--out", str(tmp_path / "forecast.parquet")])
    assert loads == []


def test_sync_passes_the_operators_skipped_archives_to_the_replay(monkeypatch, tmp_path, capsys):
    key = "static/8/gtfs_static_20260601.zip"
    monkeypatch.setenv("OBJECT_STORE_BUCKET", "bucket")
    monkeypatch.setenv("OBJECT_STORE_ENDPOINT", "https://r2.example")
    monkeypatch.setattr(cli, "_capture", lambda cmd: f"2026-06-01 00:00:00 1234 {key}\n")
    ran: list[list[str]] = []
    monkeypatch.setattr(cli, "_run", lambda cmd: ran.append(list(cmd)))

    code = main(["sync", "--state", str(tmp_path / "s.json"), "--work", str(tmp_path / "w"), "--skip-static", key])

    assert code == 0
    assert not [c for c in ran if "load_static" in c]
    assert key in capsys.readouterr().err


def test_use_store_credentials_swaps_in_the_write_capable_pair(monkeypatch):
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "archive-readonly-key")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "archive-readonly-secret")
    monkeypatch.setenv("ML_STORE_ACCESS_KEY_ID", "store-write-key")
    monkeypatch.setenv("ML_STORE_SECRET_ACCESS_KEY", "store-write-secret")

    cli._use_store_credentials()

    assert os.environ["AWS_ACCESS_KEY_ID"] == "store-write-key"
    assert os.environ["AWS_SECRET_ACCESS_KEY"] == "store-write-secret"


def test_use_store_credentials_rejects_an_unset_but_present_variable(monkeypatch):
    # systemd's EnvironmentFile= sets an empty-but-present var for `VAR=`,
    # exactly what env.example ships as a template -- os.environ[...] would
    # return "" rather than raise, silently swapping in an empty credential.
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "archive-readonly-key")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "archive-readonly-secret")
    monkeypatch.setenv("ML_STORE_ACCESS_KEY_ID", "")
    monkeypatch.setenv("ML_STORE_SECRET_ACCESS_KEY", "store-write-secret")

    with pytest.raises(SystemExit):
        cli._use_store_credentials()

    assert os.environ["AWS_ACCESS_KEY_ID"] == "archive-readonly-key"


def test_snapshot_runs_saves_the_replicas_runs_for_a_later_variant_eval(tmp_path, monkeypatch):
    from ml.dataset import read_runs

    monkeypatch.setattr("ml.dataset.load_runs", lambda client, agency_ids: _runs())
    monkeypatch.setattr("ml.cli.get_client", lambda: object())
    out = tmp_path / "runs.parquet"
    assert main(["snapshot-runs", "--out", str(out), "--agency", "8"]) == 0
    # Parquet has no seconds resolution, so the dates come back in milliseconds.
    pd.testing.assert_frame_equal(read_runs(out).astype({"service_date": "datetime64[s]"}), _runs())


def _snapshot(tmp_path):
    from ml.dataset import write_runs

    path = tmp_path / "runs.parquet"
    write_runs(_runs(), path)
    return path


def test_variant_eval_scores_each_point_of_one_fit_on_the_same_origins(tmp_path):
    from ml.model_result import result_from_json

    out = tmp_path / "variants"
    args = ["variant-eval", "--runs", str(_snapshot(tmp_path)), "--fit", "clip", "--out-dir", str(out)]
    assert main([*args, "--point", "mean", "--point", "q50", "--origins", "7"]) == 0
    mean = result_from_json((out / "clip.json").read_text())
    median = result_from_json((out / "clip-q50.json").read_text())
    assert mean.origins == median.origins and len(mean.origins) == 7
    assert mean.params["clip_quantile"] == median.params["clip_quantile"] is not None
    assert (mean.params["point"], median.params["point"]) == ("mean", "q50")


def test_variant_eval_refuses_a_fit_it_does_not_know(tmp_path):
    with pytest.raises(SystemExit):
        main(["variant-eval", "--runs", "x.parquet", "--fit", "lasso", "--out-dir", str(tmp_path)])


@pytest.mark.parametrize("fit", ["huber", "clip-huber"])
def test_variant_eval_refuses_a_mean_objective_fit_scored_without_the_mean(tmp_path, fit):
    # The mean objective shapes only the mean model, so scoring the median
    # alone would write the plain fit's result under the Huber name.
    with pytest.raises(SystemExit, match="--point mean"):
        main(["variant-eval", "--runs", "x.parquet", "--fit", fit, "--point", "q50", "--out-dir", str(tmp_path)])


def test_compare_summarizes_every_variant_in_the_directory(tmp_path, capsys):
    import json

    from ml.compare import variant_name
    from ml.models import ModelParams

    out = tmp_path / "variants"
    snapshot = str(_snapshot(tmp_path))
    assert main(["variant-eval", "--runs", snapshot, "--fit", "base", "--out-dir", str(out), "--origins", "7"]) == 0
    huber = ["variant-eval", "--runs", snapshot, "--fit", "huber", "--point", "mean", "--out-dir", str(out)]
    assert main([*huber, "--origins", "7"]) == 0
    assert main(["compare", "--dir", str(out)]) == 0
    comparison = json.loads((out / "comparison.json").read_text())
    # Unless told otherwise, variants are scored with, and compared against, what is published.
    live = variant_name("base", ModelParams().point)
    assert set(comparison["variants"]) == {live, "huber"} and comparison["baseline"] == live
    assert (out / "comparison.md").read_text() == capsys.readouterr().out
    # A second compare reads the variants again, not its own output.
    assert main(["compare", "--dir", str(out)]) == 0


def _adopted_backtest(tmp_path, monkeypatch, params):
    """A backtest result recording `params`, judged adopted, with train's
    stores stubbed out. Returns the backtest's path."""
    from ml.model_result import ModelBacktestResult, result_to_json

    backtest = tmp_path / "models.json"
    backtest.write_text(
        result_to_json(
            ModelBacktestResult(
                params=params, origins=[START], cutoffs=[START], agencies=[], intervals={}, origin_skill=[]
            )
        )
    )
    monkeypatch.setattr("ml.cli.judge", lambda result: __import__("ml.adoption").adoption.Verdict(True, ()))
    monkeypatch.setattr("ml.dataset.load_runs", lambda client, agency_ids: _runs())
    monkeypatch.setattr("ml.cli.get_client", lambda: object())
    monkeypatch.setattr("ml.cli._today_jst", lambda: START + timedelta(days=DAYS + 1))
    monkeypatch.setattr("ml.cli._run", lambda cmd: None)
    for name, value in (
        ("ML_STORE_BUCKET", "transit-ml"),
        ("ML_STORE_ENDPOINT", "https://example.r2"),
        ("ML_STORE_ACCESS_KEY_ID", "store-key"),
        ("ML_STORE_SECRET_ACCESS_KEY", "store-secret"),
        ("AWS_ACCESS_KEY_ID", "unused"),
        ("AWS_SECRET_ACCESS_KEY", "unused"),
    ):
        monkeypatch.setenv(name, value)
    return backtest


def _spy_fit(monkeypatch):
    import ml.models

    fitted = []
    real = ml.models.fit_calibrated

    def spy(frame, params, cutoff, **kwargs):
        fitted.append(params)
        return real(frame, params, cutoff, **kwargs)

    monkeypatch.setattr(ml.models, "fit_calibrated", spy)
    return fitted


def test_train_fits_and_publishes_exactly_the_params_the_backtest_judged(tmp_path, monkeypatch):
    from dataclasses import asdict

    from ml.model_store import read_meta
    from ml.models import ModelParams

    judged = ModelParams(window_days=21, rounds=40, num_leaves=7, min_data_in_leaf=5, clip_quantile=0.99, point="mean")
    judged = ModelParams(**{**asdict(judged), "mean_objective": "huber", "huber_delta": 3.0})
    backtest = _adopted_backtest(tmp_path, monkeypatch, asdict(judged))
    fitted = _spy_fit(monkeypatch)
    out = tmp_path / "model"
    assert main(["train", "--backtest", str(backtest), "--out", str(out), "--agency", "8"]) == 0
    assert fitted == [judged]
    assert (out / "mean.txt").exists()
    meta = read_meta(out)
    assert (meta["point"], meta["clip_quantile"], meta["window_days"]) == ("mean", 0.99, 21)


def test_train_reads_a_record_from_before_the_point_existed_as_an_unclipped_mean(tmp_path, monkeypatch):
    from dataclasses import asdict

    from ml.models import ModelParams

    legacy = asdict(ModelParams(rounds=40, num_leaves=7, min_data_in_leaf=5))
    for name in ("clip_quantile", "point", "mean_objective", "huber_delta"):
        del legacy[name]
    backtest = _adopted_backtest(tmp_path, monkeypatch, legacy)
    fitted = _spy_fit(monkeypatch)
    assert main(["train", "--backtest", str(backtest), "--out", str(tmp_path / "model"), "--agency", "8"]) == 0
    assert (fitted[0].point, fitted[0].clip_quantile, fitted[0].mean_objective) == ("mean", None, "regression")


def test_train_refuses_a_record_that_does_not_say_what_was_judged(tmp_path, monkeypatch):
    backtest = _adopted_backtest(tmp_path, monkeypatch, {})
    with pytest.raises(ValueError, match="window_days"):
        main(["train", "--backtest", str(backtest), "--out", str(tmp_path / "model"), "--agency", "8"])


def test_train_takes_no_window_of_its_own(tmp_path):
    with pytest.raises(SystemExit):
        main(["train", "--backtest", str(tmp_path / "models.json"), "--out", str(tmp_path / "m"), "--window", "14"])


def _rewrite_meta(model_dir, **changes):
    import json

    path = model_dir / "meta.json"
    meta = {**json.loads(path.read_text()), **changes}
    path.write_text(json.dumps({key: value for key, value in meta.items() if value is not ...}))


def test_predict_publishes_the_point_forecast_the_model_was_adopted_with(tmp_path, monkeypatch):
    raw = _runs()
    model_dir = tmp_path / "model"
    _publish_model(raw, model_dir)
    _rewrite_meta(model_dir, point="q50")
    _stub_predict(monkeypatch, raw, START + timedelta(days=DAYS))
    out = tmp_path / "forecast.parquet"
    assert main(["predict", "--model", str(model_dir), "--out", str(out), "--agency", "8"]) == 0
    forecast = pd.read_parquet(out)
    np.testing.assert_allclose(forecast["mean_min"], forecast["q50"])


def test_predict_builds_features_with_the_clip_the_model_was_fit_with(tmp_path, monkeypatch):
    import ml.features

    raw = _runs()
    model_dir = tmp_path / "model"
    _publish_model(raw, model_dir)
    _rewrite_meta(model_dir, clip_quantile=0.99)
    _stub_predict(monkeypatch, raw, START + timedelta(days=DAYS))
    seen = []
    real = ml.features.build_frame

    def spy(*args, **kwargs):
        seen.append(kwargs.get("clip_quantile"))
        return real(*args, **kwargs)

    monkeypatch.setattr(ml.features, "build_frame", spy)
    assert main(["predict", "--model", str(model_dir), "--out", str(tmp_path / "f.parquet"), "--agency", "8"]) == 0
    assert seen == [0.99]


def test_a_model_published_without_point_or_clip_predicts_its_mean_unclipped(tmp_path, monkeypatch):
    raw = _runs()
    model_dir = tmp_path / "model"
    _publish_model(raw, model_dir, point="mean")
    _stub_predict(monkeypatch, raw, START + timedelta(days=DAYS))
    current = tmp_path / "current.parquet"
    assert main(["predict", "--model", str(model_dir), "--out", str(current), "--agency", "8"]) == 0
    _rewrite_meta(model_dir, point=..., clip_quantile=...)
    legacy = tmp_path / "legacy.parquet"
    assert main(["predict", "--model", str(model_dir), "--out", str(legacy), "--agency", "8"]) == 0
    columns = ["agency_id", "route_code", "service_date", "hour", "mean_min", "q10", "q50", "q90"]
    pd.testing.assert_frame_equal(pd.read_parquet(legacy)[columns], pd.read_parquet(current)[columns])
    assert not np.allclose(pd.read_parquet(legacy)["mean_min"], pd.read_parquet(legacy)["q50"])
