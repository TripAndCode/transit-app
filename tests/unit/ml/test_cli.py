from datetime import date, timedelta

import pytest

from ml import cli
from ml.backtest import evaluate_agency, results_to_json
from ml.cells import Cell
from ml.cli import main


def test_unknown_commands_are_refused():
    with pytest.raises(SystemExit):
        main(["train"])


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
