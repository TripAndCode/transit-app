"""Which reports read the aggregates and which scan the observations live.

The compute functions branch on the same predicates, so a report's definition
can only name a build time when its rows came from the aggregates.
"""

from dataclasses import replace
from datetime import date

import pytest

from api.range import RangeCtx
from api.routers.reports import _REPORT_TYPES
from pipeline.reports.rankings import aggregates_serve, hourly_aggregates_serve, report_reads_aggregates

_ALL = RangeCtx(from_date=date(2026, 11, 1), to_date=date(2026, 11, 7))
_BAND = replace(_ALL, time_band="evening")
_ROUTES = replace(_ALL, routes=("R1",))

_TIME_BAND_LIVE = [
    "route_table",
    "ranking",
    "ranking_best",
    "on_time",
    "worst_5min",
    "trend",
    "compare_ranking",
    "dow_weekend",
    "dow_weekday",
    "council_summary",
]


def test_the_aggregates_keep_no_hour_of_day():
    assert aggregates_serve(_ALL) and aggregates_serve(_ROUTES)
    assert not aggregates_serve(_BAND)


def test_the_hourly_aggregate_pools_every_route():
    assert hourly_aggregates_serve(_ALL)
    assert not hourly_aggregates_serve(_ROUTES)
    assert not hourly_aggregates_serve(_BAND)


@pytest.mark.parametrize("report_type", _TIME_BAND_LIVE)
def test_a_time_band_sends_a_report_to_the_live_scan(report_type):
    assert report_reads_aggregates(report_type, _ALL)
    assert not report_reads_aggregates(report_type, _BAND)


def test_trend_carries_the_hourly_heatmap_which_a_route_filter_scans_live():
    assert not report_reads_aggregates("trend", _ROUTES)
    assert report_reads_aggregates("ranking", _ROUTES)


def test_the_delay_record_always_scans_live():
    assert not report_reads_aggregates("delay_certificate", _ALL)


def test_dwell_run_has_no_live_scan():
    """A time band leaves it unavailable rather than scanned live."""
    assert report_reads_aggregates("dwell_run", _BAND)


def test_every_report_type_is_answered():
    for report_type in _REPORT_TYPES:
        report_reads_aggregates(report_type, _ALL)
    with pytest.raises(ValueError):
        report_reads_aggregates("not_a_report", _ALL)
