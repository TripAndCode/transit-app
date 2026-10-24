"""`gtfs_pipeline.py ingest_ml`: an operator-run pull of the latest ML
forecast. Unlike the cron sweep it fails loudly, since nobody reads a log
line from a command they just typed."""

from __future__ import annotations

import logging
from argparse import Namespace
from unittest.mock import MagicMock, patch

import pytest

import gtfs_pipeline
from pipeline import ml_forecast_ingest
from pipeline.ml_forecast_ingest import ForecastIngest, ForecastReadError, ForecastWriteError, StoreNotConfigured


def _run(*, load_result=None, load_error=None):
    """Runs the command against a fake connection; returns it and the
    autocommit setting load_forecast saw."""
    conn = MagicMock()
    conn.autocommit = True  # what _get_conn() hands back
    seen_autocommit = []

    def fake_load(c, s3, bucket):
        seen_autocommit.append(c.autocommit)
        if load_error:
            raise load_error
        return load_result

    with (
        patch.object(gtfs_pipeline, "_get_conn", return_value=conn),
        patch.object(ml_forecast_ingest, "s3_client_from_env", return_value=(object(), "transit-ml")),
        patch.object(ml_forecast_ingest, "load_forecast", side_effect=fake_load),
    ):
        try:
            gtfs_pipeline.cmd_ingest_ml(Namespace())
        finally:
            conn.close.assert_called_once()
    return conn, seen_autocommit


def test_reports_the_rows_and_the_forecast_it_ingested(caplog):
    with caplog.at_level(logging.INFO):
        _, seen = _run(load_result=ForecastIngest(prefix="forecasts/2026-10-24", rows=1234))
    assert "upserted 1234 row(s) from forecasts/2026-10-24" in caplog.text
    # One transaction, so a failed upsert rolls the prune back with it.
    assert seen == [False]


def test_says_nothing_is_published_rather_than_zero_rows(caplog):
    with caplog.at_level(logging.INFO):
        _run(load_result=ForecastIngest(prefix=None, rows=0))
    assert "no forecast has been published yet" in caplog.text
    assert "upserted" not in caplog.text


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
