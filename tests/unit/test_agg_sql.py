"""The SQL analyze executes for its inline builders, rendered without a database.

These pin the rendering the builders rely on: ClickHouse parameters reach the
server unescaped, the date filter lands in every subquery that scans `updates`,
and module constants are interpolated with the values their helpers produce.
"""

import pytest

from pipeline import agg_sql
from pipeline.db import hms_to_sec_sql
from pipeline.dwell_run import DWELL_HI, DWELL_LO, DWELL_WIDTH, RUN_HI, RUN_LO, RUN_WIDTH
from pipeline.headways import HIGH_FREQUENCY_HEADWAY_SEC, dominant_service_ctes
from pipeline.histogram import bucket_case_sql, hist_array_sql
from pipeline.service_day import service_date_ch, service_day_offset_ch, service_day_seconds_sql

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
def test_the_date_filter_is_the_only_part_that_varies_by_run(name):
    build, _ = CH_BUILDERS[name]
    assert build(DATES).replace(DATES, "") == build("")


@pytest.mark.parametrize("name", CH_BUILDERS)
def test_service_date_is_the_shared_service_day_expression(name):
    build, _ = CH_BUILDERS[name]
    assert service_date_ch() in build("")


def test_headway_daily_places_the_schedule_on_the_service_day_before_the_delay():
    sql = agg_sql.route_headway_daily_ch_sql("")
    schedule = service_day_offset_ch("tupleElement(latest, 2)")
    assert "groupArray(toInt64(" + schedule + ") + toInt64(tupleElement(latest, 1)))" in sql


def test_feed_health_counts_clamped_rows_with_the_server_side_bound():
    assert "countIf(abs(dep_delay) > {max_delay:Int32})" in agg_sql.feed_health_ch_sql("")


def test_headway_daily_bounds_the_delay_with_server_side_parameters():
    sql = agg_sql.route_headway_daily_ch_sql("")
    assert "BETWEEN {min_delay:Int32} AND {max_delay:Int32}" in sql


PG_BUILDERS = {
    "route_headway": lambda: agg_sql.route_headway_sql(),
    "dwell_run": lambda: agg_sql.dwell_run_sql(),
    "static_version_summary": lambda: agg_sql.STATIC_VERSION_SUMMARY_SQL,
}


@pytest.mark.parametrize("name", PG_BUILDERS)
def test_postgres_statements_bind_the_agency_by_name(name):
    sql = PG_BUILDERS[name]()
    assert "%(agency_id)s" in sql
    assert sql.replace("%(agency_id)s", "").count("%") == 0


def test_route_headway_reads_the_dominant_service_and_the_high_frequency_bound():
    sql = agg_sql.route_headway_sql()
    assert dominant_service_ctes("%(agency_id)s") in sql
    assert service_day_seconds_sql(hms_to_sec_sql("sst.departure_time")) in sql
    assert f"scheduled_headway_median_sec <= {HIGH_FREQUENCY_HEADWAY_SEC} AS is_high_frequency" in sql


def test_dwell_run_buckets_with_the_shared_histogram_bounds():
    sql = agg_sql.dwell_run_sql()
    assert bucket_case_sql("dwell_sec", lo=DWELL_LO, hi=DWELL_HI, width=DWELL_WIDTH) in sql
    assert bucket_case_sql("running_sec", lo=RUN_LO, hi=RUN_HI, width=RUN_WIDTH) in sql
    assert hist_array_sql("bd", lo=DWELL_LO, hi=DWELL_HI, width=DWELL_WIDTH) in sql
    assert hist_array_sql("br", lo=RUN_LO, hi=RUN_HI, width=RUN_WIDTH) in sql


def test_dwell_run_places_scheduled_times_on_the_service_day():
    sql = agg_sql.dwell_run_sql()
    assert service_day_seconds_sql(hms_to_sec_sql("d.sched_arrival_time")) + " AS sched_arr_sec" in sql
    assert service_day_seconds_sql(hms_to_sec_sql("d.sched_departure_time")) + " AS sched_dep_sec" in sql


def test_static_version_summary_upsert_keeps_its_positional_parameters():
    assert agg_sql.STATIC_VERSION_SUMMARY_UPSERT_SQL.count("%s") == 4
