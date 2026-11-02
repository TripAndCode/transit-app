"""The SQL analyze executes for its inline builders, rendered without a database.

These pin the rendering the builders rely on: ClickHouse parameters reach the
server unescaped, the date filter lands in every subquery that scans `updates`,
and module constants are interpolated with the values their helpers produce.
"""

import pytest

from pipeline import agg_sql
from pipeline.service_day import service_date_ch, service_day_offset_ch

DATES = " AND <DATE-FILTER>"

CH_BUILDERS = {
    "feed_health": (agg_sql.feed_health_ch_sql, 1),
    "service_delivered": (agg_sql.service_delivered_ch_sql, 2),
    "schedule_revision": (agg_sql.schedule_revision_ch_sql, 1),
    "route_headway_daily": (agg_sql.route_headway_daily_ch_sql, 1),
}


@pytest.mark.parametrize("name", CH_BUILDERS)
def test_date_filter_lands_in_every_scan_of_updates(name):
    build, scans = CH_BUILDERS[name]
    assert build(DATES).count(DATES) == scans


@pytest.mark.parametrize("name", CH_BUILDERS)
def test_clickhouse_parameters_reach_the_server_unescaped(name):
    build, _ = CH_BUILDERS[name]
    sql = build("")
    assert "{agency_id:UInt16}" in sql
    assert "{{" not in sql and "}}" not in sql


@pytest.mark.parametrize("name", CH_BUILDERS)
def test_a_full_rebuild_renders_without_a_date_filter(name):
    build, _ = CH_BUILDERS[name]
    assert "<DATE-FILTER>" not in build("")


@pytest.mark.parametrize("name", CH_BUILDERS)
def test_service_date_is_the_shared_service_day_expression(name):
    build, _ = CH_BUILDERS[name]
    assert service_date_ch() in build("")


def test_headway_daily_places_the_schedule_on_the_service_day_before_the_delay():
    sql = agg_sql.route_headway_daily_ch_sql("")
    assert "groupArray(toInt64(" + service_day_offset_ch("scheduled_sec") + ") + toInt64(dep_delay))" in sql


def test_feed_health_counts_clamped_rows_with_the_server_side_bound():
    assert "countIf(abs(dep_delay) > {max_delay:Int32})" in agg_sql.feed_health_ch_sql("")


def test_headway_daily_bounds_the_delay_with_server_side_parameters():
    sql = agg_sql.route_headway_daily_ch_sql("")
    assert "BETWEEN {min_delay:Int32} AND {max_delay:Int32}" in sql
