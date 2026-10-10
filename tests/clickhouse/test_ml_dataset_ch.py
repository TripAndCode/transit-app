"""Trip runs read from ClickHouse into the frame the models learn from."""

import os
from datetime import datetime, timezone

import pytest

from tests.unit.ml.ml_group import require

pd = require("pandas")

from ml.dataset import RUN_COLUMNS, fetch_runs  # noqa: E402

pytestmark = pytest.mark.skipif(os.environ.get("RUN_CH_INTEGRATION") != "1", reason="requires `make ch-test`")

COLUMNS = [
    "agency_id",
    "captured_at",
    "file_name",
    "trip_id",
    "service_type",
    "scheduled_time",
    "route_code",
    "stop_sequence",
    "dep_delay",
    "scheduled_sec",
]
EIGHT_O_FIVE = 8 * 3600 + 5 * 60


def _utc(y, m, d, hh, mm):
    return datetime(y, m, d, hh, mm, tzinfo=timezone.utc)


def test_a_run_carries_its_service_stops_and_scheduled_span(ch_client):
    rows = [
        (8, _utc(2026, 5, 31, 23, 0), "a/1.pb", "T1", "svc-wk", "08:05", "R1", 1, 60, EIGHT_O_FIVE),
        (8, _utc(2026, 5, 31, 23, 20), "a/2.pb", "T1", "svc-wk", "08:20", "R1", 2, 180, EIGHT_O_FIVE + 900),
        (8, _utc(2026, 5, 31, 23, 40), "a/3.pb", "T1", "svc-wk", "08:40", "R1", 3, 120, EIGHT_O_FIVE + 2100),
    ]
    ch_client.insert("updates", rows, column_names=COLUMNS)
    runs = fetch_runs(ch_client, 8)
    assert list(runs.columns) == RUN_COLUMNS
    run = runs.iloc[0]
    assert (int(run.agency_id), run.route_code, run.trip_id, int(run.hour), run.service, int(run.stops)) == (
        8,
        "R1",
        "T1",
        8,
        "svc-wk",
        3,
    )
    assert run.delay_min == pytest.approx(2.0)
    assert run.span_min == pytest.approx(35.0)
    assert run.service_date == pd.Timestamp("2026-06-01")


def test_a_feed_without_scheduled_sec_has_no_span(ch_client):
    rows = [(1, _utc(2026, 5, 31, 23, 0), "c/1.pb", "A1", "weekday", "08:05", "A", 1, 120, None)]
    ch_client.insert("updates", rows, column_names=COLUMNS)
    runs = fetch_runs(ch_client, 1)
    assert len(runs) == 1
    assert pd.isna(runs.iloc[0].span_min)


def test_an_agency_without_rows_has_an_empty_frame_with_every_column(ch_client):
    runs = fetch_runs(ch_client, 99)
    assert runs.empty
    assert list(runs.columns) == RUN_COLUMNS
