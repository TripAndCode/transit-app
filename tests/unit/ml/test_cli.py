from datetime import date, timedelta

import pytest

from ml.backtest import evaluate_agency, results_to_json
from ml.cells import Cell
from ml.cli import main


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
    # No agencies, no intervals: judge() reads this as "unmeasured" on every
    # criterion, which is "not adopted" -- the cheapest fixture that is
    # reliably rejected without needing a real skill/coverage computation.
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
