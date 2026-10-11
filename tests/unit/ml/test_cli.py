from datetime import date, timedelta

import pytest

from tests.unit.ml.ml_group import require

pd = require("pandas")
np = require("numpy")
require("lightgbm")

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
    from datetime import date

    from ml.backtest import AgencyResult
    from ml.model_result import ModelBacktestResult, result_to_json

    backtest = tmp_path / "models.json"
    # One agency, but with no slice rows and no intervals: summarize()'s
    # skill_vs_b0 and merged_intervals()'s coverage both come back None
    # (unmeasured), which judge() reads as "not adopted" on every criterion --
    # the cheapest fixture that is reliably rejected without a real skill/
    # coverage computation.
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


def test_train_fits_and_publishes_the_model_when_adopted(tmp_path, monkeypatch):
    import json as json_module

    from ml.adoption import Verdict
    from ml.backtest import AgencyResult
    from ml.model_result import ModelBacktestResult, result_to_json

    backtest = tmp_path / "models.json"
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
    monkeypatch.setattr("ml.cli.judge", lambda result: Verdict(adopted=True, reasons=()))
    monkeypatch.setattr("ml.dataset.load_runs", lambda client, agency_ids: _runs())
    monkeypatch.setattr("ml.cli.get_client", lambda: object())
    monkeypatch.setattr("ml.cli._today_jst", lambda: START + timedelta(days=DAYS + 1))
    calls = []
    monkeypatch.setattr("ml.cli._run", calls.append)
    monkeypatch.setenv("ML_STORE_BUCKET", "transit-ml")
    monkeypatch.setenv("ML_STORE_ENDPOINT", "https://example.r2")
    monkeypatch.setenv("ML_STORE_ACCESS_KEY_ID", "store-key")
    monkeypatch.setenv("ML_STORE_SECRET_ACCESS_KEY", "store-secret")

    out = tmp_path / "model"
    assert main(["train", "--backtest", str(backtest), "--out", str(out), "--agency", "8"]) == 0

    meta = json_module.loads((out / "meta.json").read_text())
    assert meta["version"]
    assert (out / "routes.parquet").exists()
    assert len(calls) == 2  # upload_dir (the model) + upload_file (latest.json)


def test_predict_writes_one_row_per_future_cell_for_an_agency_with_enough_history(tmp_path, monkeypatch):
    from ml.features import route_ids, with_route_ids
    from ml.model_store import save_model_set
    from ml.models import ModelParams, fit_calibrated
    from ml.training import training_frame

    raw = _runs()
    routes = route_ids(raw)
    runs = with_route_ids(raw, routes)
    cutoff = START + timedelta(days=DAYS - 1)
    train = training_frame(runs, cutoff, window_days=28, half_life_days=28)
    models = fit_calibrated(train, ModelParams(rounds=40, num_leaves=7, min_data_in_leaf=5), cutoff)

    model_dir = tmp_path / "model"
    save_model_set(models, model_dir, version=cutoff.isoformat(), routes=routes)

    origin = START + timedelta(days=DAYS + 4)
    monkeypatch.setattr("ml.dataset.load_runs", lambda client, agency_ids: raw)
    monkeypatch.setattr("ml.cli.get_client", lambda: object())
    monkeypatch.setattr("ml.cli._today_jst", lambda: origin)
    calls = []
    monkeypatch.setattr("ml.cli._run", calls.append)
    monkeypatch.setenv("ML_STORE_BUCKET", "transit-ml")
    monkeypatch.setenv("ML_STORE_ENDPOINT", "https://example.r2")
    monkeypatch.setenv("ML_STORE_ACCESS_KEY_ID", "store-key")
    monkeypatch.setenv("ML_STORE_SECRET_ACCESS_KEY", "store-secret")

    out = tmp_path / "forecast.parquet"
    assert main(["predict", "--model", str(model_dir), "--out", str(out), "--agency", "8"]) == 0

    forecast = pd.read_parquet(out)
    assert len(forecast) > 0
    assert not forecast.duplicated(["agency_id", "route_code", "service_date", "hour"]).any()
    assert forecast["service_date"].min() > pd.Timestamp(origin)
    assert forecast["service_date"].max() <= pd.Timestamp(origin) + pd.Timedelta(days=7)
    assert len(calls) == 2  # upload_file (the parquet) + upload_file (manifest.json)


def test_predict_publishes_nothing_for_an_agency_below_the_model_history_floor(tmp_path, monkeypatch):
    from ml.features import route_ids, with_route_ids
    from ml.model_store import save_model_set
    from ml.models import ModelParams, fit_calibrated
    from ml.training import training_frame

    raw = _runs()[lambda f: f["service_date"] >= pd.Timestamp(START + timedelta(days=DAYS - 30))]
    routes = route_ids(raw)
    runs = with_route_ids(raw, routes)
    cutoff = START + timedelta(days=DAYS - 1)
    train = training_frame(runs, cutoff, window_days=28, half_life_days=28)
    models = fit_calibrated(train, ModelParams(rounds=40, num_leaves=7, min_data_in_leaf=5), cutoff)

    model_dir = tmp_path / "model"
    save_model_set(models, model_dir, version=cutoff.isoformat(), routes=routes)

    origin = START + timedelta(days=DAYS + 4)
    monkeypatch.setattr("ml.dataset.load_runs", lambda client, agency_ids: raw)
    monkeypatch.setattr("ml.cli.get_client", lambda: object())
    monkeypatch.setattr("ml.cli._today_jst", lambda: origin)
    monkeypatch.setattr("ml.cli._run", lambda cmd: None)
    monkeypatch.setenv("ML_STORE_BUCKET", "transit-ml")
    monkeypatch.setenv("ML_STORE_ENDPOINT", "https://example.r2")
    monkeypatch.setenv("ML_STORE_ACCESS_KEY_ID", "store-key")
    monkeypatch.setenv("ML_STORE_SECRET_ACCESS_KEY", "store-secret")

    out = tmp_path / "forecast.parquet"
    assert main(["predict", "--model", str(model_dir), "--out", str(out), "--agency", "8"]) == 0
    assert pd.read_parquet(out).empty
