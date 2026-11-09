"""The monthly feed-quality scorecard's shaping and month planning, without a database."""

from datetime import date

import pytest

from pipeline import feed_quality
from pipeline.feed_quality import COLUMNS, plan_months, shape, through_of
from pipeline.reports.comparability import assess
from pipeline.strategies.static_join import RT_COVERAGE_FIELDS

_OCT = date(2026, 10, 1)


def _service(observed=900, coverage_planned=1000, covered_days=31):
    return {
        "planned": coverage_planned,
        "covered_days": covered_days,
        "non_executed": 0,
        "observed": observed,
        "coverage_planned": coverage_planned,
        "ingest_strategy": "static_join",
        "reports_cancellations": False,
    }


def _day(stop_time_updates=100, stop_events=40, **fields):
    counts = {f"{field}_rows": fields.get(field, stop_time_updates) for field in RT_COVERAGE_FIELDS}
    return {"stop_time_updates": stop_time_updates, "stop_events": stop_events, **counts}


def _shape(**over):
    args = {
        "agency_id": 8,
        "month": _OCT,
        "through": date(2026, 10, 31),
        "ingest_strategy": "static_join",
        "service": _service(),
        "stop_plan": {date(2026, 10, 1): 50, date(2026, 10, 2): 50},
        "day_rows": {date(2026, 10, 1): _day(stop_events=45), date(2026, 10, 2): _day(stop_events=30)},
        "live_polls": 4,
        "polls": 4,
        "ages": (10.0, 35.5),
        "raw_samples": 1000,
        "clamp_count": 3,
        "total_rows": 1200,
    }
    return shape(**{**args, **over})


def test_a_row_names_every_column():
    assert set(_shape()) == set(COLUMNS)


def test_rt_completeness_is_the_gates_coverage():
    row = _shape(service=_service(observed=693, coverage_planned=1000))
    gate = assess(
        ingest_strategy="static_join",
        observed_trips=693,
        coverage_planned_trips=1000,
        on_time_count=0,
        samples=1,
        clamp_pct=None,
    )
    assert (row["rt_completeness_pct"], row["rt_completeness_missing"]) == (gate.coverage_pct, None)
    assert (row["observed_trips"], row["planned_trips"]) == (693, 1000)


def test_unmatched_trips_leave_both_completeness_figures_unknown_with_the_gates_reason():
    row = _shape(ingest_strategy="aomori_regex")
    assert row["rt_completeness_pct"] is None and row["rt_completeness_missing"] == "coverage_unknown"
    assert row["stop_completeness_pct"] is None and row["stop_completeness_missing"] == "coverage_unknown"
    assert row["observed_trips"] is None and row["planned_trips"] is None


def test_a_window_without_a_plan_reads_no_plan_not_zero():
    row = _shape(service=_service(observed=500, coverage_planned=0, covered_days=0), stop_plan={})
    assert (row["rt_completeness_pct"], row["rt_completeness_missing"]) == (None, "no_plan")
    assert (row["stop_completeness_pct"], row["stop_completeness_missing"]) == (None, "no_plan")


def test_a_planned_window_the_feed_never_reported_reads_zero():
    row = _shape(service=_service(observed=0), day_rows={})
    assert row["rt_completeness_pct"] == 0.0
    assert row["stop_completeness_pct"] == 0.0


def test_stop_events_count_only_the_days_whose_plan_is_known():
    row = _shape(
        stop_plan={date(2026, 10, 1): 50, date(2026, 10, 2): None},
        day_rows={date(2026, 10, 1): _day(stop_events=45), date(2026, 10, 2): _day(stop_events=30)},
    )
    assert (row["observed_stop_events"], row["planned_stop_events"], row["stop_plan_days"]) == (45, 50, 1)
    assert row["stop_completeness_pct"] == 90.0


def test_trips_planned_on_days_with_no_known_stop_plan_read_no_stop_plan():
    row = _shape(stop_plan={date(2026, 10, 1): None})
    assert (row["stop_completeness_pct"], row["stop_completeness_missing"]) == (None, "no_stop_plan")
    assert row["stop_plan_days"] == 0


def test_clamp_share_is_the_gates_and_unknown_without_readings():
    assert (_shape()["clamp_pct"], _shape()["clamp_missing"]) == (0.3, None)
    row = _shape(raw_samples=0, clamp_count=0)
    assert (row["clamp_pct"], row["clamp_missing"]) == (None, "no_data")


def test_field_coverage_is_each_fields_share_of_stop_time_updates():
    row = _shape(
        day_rows={
            date(2026, 10, 1): _day(stop_time_updates=100, arr_delay=7),
            date(2026, 10, 2): _day(stop_time_updates=100, arr_delay=8, stop_id=99),
        }
    )
    assert row["stop_time_updates"] == 200
    assert (row["arr_delay_pct"], row["stop_id_pct"], row["schedule_relationship_trip_pct"]) == (7.5, 99.5, 100.0)
    assert row["field_coverage_missing"] is None


def test_a_strategy_that_never_stores_the_fields_has_no_field_coverage():
    row = _shape(ingest_strategy="aomori_regex")
    assert row["field_coverage_missing"] == "feed_cannot_report"
    assert all(row[f"{field}_pct"] is None for field in RT_COVERAGE_FIELDS)


def test_no_stop_time_update_is_no_field_coverage_not_zero():
    row = _shape(day_rows={})
    assert row["field_coverage_missing"] == "no_data"
    assert all(row[f"{field}_pct"] is None for field in RT_COVERAGE_FIELDS)


def test_message_age_comes_from_live_polls():
    row = _shape()
    assert (row["message_age_polls"], row["message_age_p50_sec"], row["message_age_p95_sec"]) == (4, 10.0, 35.5)
    assert row["message_age_missing"] is None


@pytest.mark.parametrize(
    ("total_rows", "live_polls", "polls", "reason"),
    [(0, 0, 0, "no_data"), (1200, 0, 0, "archive_stamped"), (1200, 3, 0, "no_header")],
)
def test_message_age_without_a_measurable_poll_says_why(total_rows, live_polls, polls, reason):
    row = _shape(total_rows=total_rows, live_polls=live_polls, polls=polls, ages=None)
    assert (row["message_age_p50_sec"], row["message_age_p95_sec"], row["message_age_missing"]) == (None, None, reason)


def test_vehicle_positions_are_not_recorded():
    row = _shape()
    assert (row["vehicle_positions_available"], row["vehicle_positions_missing"]) == (None, "not_recorded")


def test_the_month_in_progress_counts_through_the_last_closed_day():
    assert through_of(date(2026, 11, 1), date(2026, 11, 8)) == date(2026, 11, 8)
    assert through_of(_OCT, date(2026, 11, 8)) == date(2026, 10, 31)
    assert through_of(date(2024, 2, 1), date(2024, 11, 8)) == date(2024, 2, 29)


def test_a_full_rebuild_builds_every_month_from_the_first_observed_one():
    purge, build = plan_months(None, date(2026, 9, 10), date(2026, 11, 8), {})
    assert purge is None
    assert build == [date(2026, 9, 1), _OCT, date(2026, 11, 1)]


def test_an_incremental_run_builds_the_changed_months_and_the_ones_whose_window_grew():
    stored = {date(2026, 9, 1): date(2026, 9, 30), _OCT: date(2026, 10, 31), date(2026, 11, 1): date(2026, 11, 7)}
    purge, build = plan_months([date(2026, 9, 14)], date(2026, 9, 10), date(2026, 11, 8), stored)
    # September for its changed day, November because the 8th closed;
    # October already counts its whole window.
    assert purge == build == [date(2026, 9, 1), date(2026, 11, 1)]


def test_a_new_month_is_built_on_its_first_closed_day_without_a_changed_date():
    stored = {_OCT: date(2026, 10, 31)}
    assert plan_months([], date(2026, 10, 1), date(2026, 11, 1), stored) == ([date(2026, 11, 1)], [date(2026, 11, 1)])


def test_a_stored_month_outside_the_observed_months_is_purged_not_built():
    stored = {date(2026, 8, 1): date(2026, 8, 31), _OCT: date(2026, 10, 31)}
    purge, build = plan_months([], date(2026, 10, 1), date(2026, 10, 31), stored)
    assert (purge, build) == ([date(2026, 8, 1)], [])


def test_an_agency_with_no_closed_observed_day_builds_nothing():
    assert plan_months(None, None, date(2026, 11, 8), {}) == (None, [])
    assert plan_months(None, date(2026, 11, 9), date(2026, 11, 8), {}) == (None, [])


def test_the_render_names_the_gate_rules_it_applies():
    rendered = "\n".join(feed_quality.render())
    assert "def coverage_pct" in rendered and "def clamp_pct" in rendered
    assert "quantilesExactInclusive(0.5, 0.95)" in rendered
