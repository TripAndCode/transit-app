"""`gtfs_pipeline.py ingest_ml`: an operator-run pull of the latest ML
forecast. Unlike the cron sweep it fails loudly, since nobody reads a log
line from a command they just typed."""

from __future__ import annotations

import logging
from argparse import Namespace
from unittest.mock import MagicMock, patch

import pytest

import gtfs_pipeline
from pipeline import ml_evaluation_ingest, ml_forecast_ingest
from pipeline.ml_evaluation_ingest import EvaluationIngest, EvaluationReadError
from pipeline.ml_forecast_ingest import ForecastIngest, ForecastReadError, ForecastWriteError, StoreNotConfigured

_NO_SUMMARIES = EvaluationIngest(listed=0, upserted=0, skipped=())


def _run(*, load_result=None, load_error=None, eval_result=_NO_SUMMARIES, eval_error=None):
    """Runs the command against a fake connection, and checks it is closed
    whether the ingest succeeds or not. A real connection's behavior is
    covered in tests/db/test_ml_forecast_ingest_db.py."""
    conn = MagicMock()

    def fake_load(c, s3, bucket):
        if load_error:
            raise load_error
        return load_result

    def fake_evaluations(c, s3, bucket):
        if eval_error:
            raise eval_error
        return eval_result

    with (
        patch.object(gtfs_pipeline, "_get_conn", return_value=conn),
        patch.object(ml_forecast_ingest, "s3_client_from_env", return_value=(object(), "transit-ml")),
        patch.object(ml_forecast_ingest, "load_forecast", side_effect=fake_load),
        patch.object(ml_evaluation_ingest, "load_evaluations", side_effect=fake_evaluations),
    ):
        try:
            gtfs_pipeline.cmd_ingest_ml(Namespace())
        finally:
            conn.close.assert_called_once()


def test_reports_the_rows_and_the_forecast_it_ingested(caplog):
    with caplog.at_level(logging.INFO):
        _run(load_result=ForecastIngest(prefix="forecasts/2026-10-24", rows=1234))
    assert "upserted 1234 row(s) from forecasts/2026-10-24" in caplog.text


def test_says_nothing_is_published_rather_than_zero_rows(caplog):
    with caplog.at_level(logging.INFO):
        _run(load_result=ForecastIngest(prefix=None, rows=0))
    assert "no forecast has been published yet" in caplog.text
    assert "upserted 0 row(s)" not in caplog.text


@pytest.mark.parametrize("error", [ForecastReadError("AccessDenied"), ForecastWriteError("deadlock detected")])
def test_exits_nonzero_and_says_why_when_the_ingest_fails(error, caplog):
    with caplog.at_level(logging.ERROR), pytest.raises(SystemExit) as excinfo:
        _run(load_error=error)
    assert excinfo.value.code == 1
    assert str(error) in caplog.text


def test_exits_before_connecting_when_the_store_is_not_configured(caplog):
    with (
        patch.object(gtfs_pipeline, "_get_conn", side_effect=AssertionError("connected")),
        patch.object(
            ml_forecast_ingest, "s3_client_from_env", side_effect=StoreNotConfigured("not set: ML_STORE_ENDPOINT")
        ),
        caplog.at_level(logging.ERROR),
        pytest.raises(SystemExit) as excinfo,
    ):
        gtfs_pipeline.cmd_ingest_ml(Namespace())
    assert excinfo.value.code == 2
    assert "ML_STORE_ENDPOINT" in caplog.text


def test_reports_the_evaluation_summaries_it_ingested(caplog):
    summaries = EvaluationIngest(listed=2, upserted=2, skipped=())
    with caplog.at_level(logging.INFO):
        _run(load_result=ForecastIngest(prefix=None, rows=0), eval_result=summaries)
    assert "upserted 2 evaluation summary(ies)" in caplog.text


def test_names_the_summaries_it_skipped(caplog):
    summaries = EvaluationIngest(listed=3, upserted=2, skipped=("evaluations/2026-11-01/summary.json",))
    with caplog.at_level(logging.WARNING):
        _run(load_result=ForecastIngest(prefix=None, rows=0), eval_result=summaries)
    assert "evaluations/2026-11-01/summary.json" in caplog.text


def test_exits_nonzero_when_the_evaluations_cannot_be_read(caplog):
    with caplog.at_level(logging.ERROR), pytest.raises(SystemExit) as excinfo:
        _run(load_result=ForecastIngest(prefix=None, rows=0), eval_error=EvaluationReadError("AccessDenied"))
    assert excinfo.value.code == 1
    assert "EvaluationReadError: AccessDenied" in caplog.text
