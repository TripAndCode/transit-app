"""The workbench's cells come out of ClickHouse exactly as the app's reports
read delay: the latest observation per stop event, implausible delays out,
JST service dates, and hours from the schedule that keep after-midnight runs."""

import os
from datetime import date, datetime, timezone

import pytest

from ml.cells import Cell
from ml.data import agencies_with_data, date_span, fetch_cells
from pipeline.db import MAX_PLAUSIBLE_DELAY_SEC

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
IMPLAUSIBLE = MAX_PLAUSIBLE_DELAY_SEC + 1


def _utc(y, m, d, hh, mm):
    return datetime(y, m, d, hh, mm, tzinfo=timezone.utc)


ROWS = [
    # T1 on JST 2026-06-01 (UTC 05-31 23:xx). Stop 1 re-polled: the later 120s wins.
    (8, _utc(2026, 5, 31, 23, 0), "a/1.pb", "T1", "weekday", "08:05", "R1", 1, 60, EIGHT_O_FIVE),
    (8, _utc(2026, 5, 31, 23, 6), "a/2.pb", "T1", "weekday", "08:05", "R1", 1, 120, EIGHT_O_FIVE),
    (8, _utc(2026, 5, 31, 23, 20), "a/3.pb", "T1", "weekday", "08:20", "R1", 2, 180, EIGHT_O_FIVE + 900),
    # T2, same route and hour: one stop at 60s.
    (8, _utc(2026, 5, 31, 23, 30), "a/4.pb", "T2", "weekday", "08:35", "R1", 1, 60, EIGHT_O_FIVE + 1800),
    # T2's implausible stop is clamped out, not averaged in.
    (8, _utc(2026, 5, 31, 23, 40), "a/5.pb", "T2", "weekday", "08:45", "R1", 2, IMPLAUSIBLE, EIGHT_O_FIVE + 2400),
    # T3 departs after midnight on the service day: scheduled_sec 24:10, captured JST 06-02 00:15.
    (8, _utc(2026, 6, 1, 15, 15), "a/6.pb", "T3", "weekday", None, "R1", 1, 240, 24 * 3600 + 600),
    # Another agency never leaks in.
    (9, _utc(2026, 5, 31, 23, 0), "b/1.pb", "T1", "weekday", "08:05", "R1", 1, 999, EIGHT_O_FIVE),
]


@pytest.fixture
def loaded(ch_client):
    ch_client.insert("updates", ROWS, column_names=COLUMNS)
    return ch_client


def test_cells_pool_each_runs_mean_of_its_latest_stop_delays(loaded):
    cells = {(c.service_date, c.hour): c for c in fetch_cells(loaded, 8)}
    morning = cells[(date(2026, 6, 1), 8)]
    assert morning.runs == 2
    assert morning.delay_sum_min == pytest.approx((120 + 180) / 2 / 60 + 60 / 60)


def test_an_after_midnight_run_keeps_its_hour_on_its_jst_capture_date(loaded):
    cells = {(c.service_date, c.hour): c for c in fetch_cells(loaded, 8)}
    assert cells[(date(2026, 6, 2), 24)] == Cell("R1", date(2026, 6, 2), 24, 1, 4.0)


def test_fetching_in_chunks_returns_the_same_cells(loaded):
    assert fetch_cells(loaded, 8, chunk_days=1) == fetch_cells(loaded, 8, chunk_days=30)


def test_an_agency_without_rows_has_no_cells(loaded):
    assert fetch_cells(loaded, 99) == []


def test_an_agencys_span_runs_from_its_first_to_its_last_jst_day(loaded):
    assert date_span(loaded, 8) == (date(2026, 6, 1), date(2026, 6, 2))
    assert date_span(loaded, 99) is None


def test_agencies_are_listed_from_the_data(loaded):
    assert agencies_with_data(loaded) == [8, 9]


def test_a_feed_without_scheduled_sec_takes_the_hour_from_its_schedule_string(ch_client):
    # Some ingest strategies store only the "HH:MM" schedule; their runs still count.
    rows = [
        (1, _utc(2026, 5, 31, 23, 0), "c/1.pb", "A1", "weekday", "08:05", "A", 1, 120, None),
        (1, _utc(2026, 5, 31, 23, 20), "c/2.pb", "A1", "weekday", "08:20", "A", 2, 240, None),
        # Neither schedule field: no hour to place the run in, so no cell.
        (1, _utc(2026, 5, 31, 23, 30), "c/3.pb", "A2", "weekday", None, "A", 1, 60, None),
    ]
    ch_client.insert("updates", rows, column_names=COLUMNS)
    assert fetch_cells(ch_client, 1) == [Cell("A", date(2026, 6, 1), 8, 1, 3.0)]


def test_a_stop_with_no_schedule_stays_out_of_its_runs_mean(ch_client):
    # A non-timepoint stop carries a realtime delay but no schedule of its own.
    rows = [
        (8, _utc(2026, 5, 31, 23, 0), "d/1.pb", "M1", "weekday", "08:05", "R1", 1, 60, EIGHT_O_FIVE),
        (8, _utc(2026, 5, 31, 23, 10), "d/2.pb", "M1", "weekday", None, "R1", 2, 600, None),
    ]
    ch_client.insert("updates", rows, column_names=COLUMNS)
    assert fetch_cells(ch_client, 8) == [Cell("R1", date(2026, 6, 1), 8, 1, 1.0)]
