from datetime import date, timedelta

import pytest

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
