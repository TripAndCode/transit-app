"""The monthly feed-quality scorecard's shaping and month planning, without a database."""

from datetime import date

import pytest

from pipeline import feed_quality
from pipeline.feed_quality import (
    COLUMNS,
    last_counted_day,
    month_bounds,
    month_through,
    month_windows,
    plan_months,
    shape,
    through_of,
)
from pipeline.reports.comparability import assess
from pipeline.strategies.static_join import RT_COVERAGE_FIELDS

_OCT = date(2026, 10, 1)


def _service(observed=900, coverage_planned=1000, covered_days=31, planned=None):
    return {
        "planned": coverage_planned if planned is None else planned,
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
        "last": date(2026, 10, 31),
        "observed": True,
        "ingest_strategy": "static_join",
        "service": _service(),
        "stop_plan": {date(2026, 10, 1): 50, date(2026, 10, 2): 50},
        "day_rows": {date(2026, 10, 1): _day(stop_events=45), date(2026, 10, 2): _day(stop_events=30)},
        "dated_polls": 5,
        "undated_polls": 2,
        "polls": 4,
        "stale_polls": 1,
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
    # Neither side of a figure the window cannot state, as the stamp states none.
    assert (row["observed_trips"], row["planned_trips"]) == (None, None)
    assert (row["observed_stop_events"], row["planned_stop_events"]) == (None, None)


def test_a_window_planned_only_after_its_coverage_day_reads_the_boards_reason():
    """The window plans trips, but none through the newest day any listed
    agency observed: the board reads that no timetable plans the days
    observed, rather than that nothing is planned."""
    row = _shape(service=_service(observed=0, coverage_planned=0, covered_days=6, planned=24))
    assert (row["rt_completeness_pct"], row["rt_completeness_missing"]) == (None, "no_plan_observed")
    assert (row["observed_trips"], row["planned_trips"], row["covered_days"]) == (None, None, 6)


def test_completeness_counts_through_the_coverage_day_and_the_window_days_count_the_whole_month():
    row = _shape(through=date(2026, 10, 20), service=_service(observed=500, coverage_planned=600, planned=900))
    assert (row["through_date"], row["window_days"]) == (date(2026, 10, 20), 31)
    assert (row["observed_trips"], row["planned_trips"], row["rt_completeness_pct"]) == (500, 600, 83.3)


def test_a_planned_window_the_feed_was_observed_in_but_reported_none_of_reads_zero():
    row = _shape(service=_service(observed=0), day_rows={})
    assert row["rt_completeness_pct"] == 0.0
    assert row["stop_completeness_pct"] == 0.0


@pytest.mark.parametrize(
    "over",
    [
        {},
        {"ingest_strategy": "aomori_regex"},
        {"service": _service(observed=0, coverage_planned=0, covered_days=0), "stop_plan": {}},
    ],
    ids=["matched and planned", "unmatched", "unplanned"],
)
def test_a_window_without_an_analyzed_observation_reads_no_data_before_any_other_reason(over):
    """The gate's first check: without an observation neither completeness
    figure is counted, so a planned window reads no data, never 0."""
    row = _shape(observed=False, **{"service": _service(observed=0), "day_rows": {}, **over})
    assert (row["rt_completeness_pct"], row["rt_completeness_missing"]) == (None, "no_data")
    assert (row["stop_completeness_pct"], row["stop_completeness_missing"]) == (None, "no_data")
    assert (row["observed_trips"], row["planned_trips"]) == (None, None)
    assert (row["observed_stop_events"], row["planned_stop_events"]) == (None, None)


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


def test_message_age_comes_from_the_polls_whose_capture_instant_is_known():
    row = _shape()
    assert (row["message_age_polls"], row["message_age_p50_sec"], row["message_age_p95_sec"]) == (4, 10.0, 35.5)
    assert (row["message_age_missing"], row["message_age_undated_polls"]) == (None, 2)


@pytest.mark.parametrize(
    ("total_rows", "dated_polls", "polls", "reason"),
    [(0, 0, 0, "no_data"), (1200, 0, 0, "archive_stamped"), (1200, 3, 0, "no_header")],
)
def test_message_age_without_a_measurable_poll_says_why(total_rows, dated_polls, polls, reason):
    row = _shape(total_rows=total_rows, dated_polls=dated_polls, polls=polls, ages=None)
    assert (row["message_age_p50_sec"], row["message_age_p95_sec"], row["message_age_missing"]) == (None, None, reason)
    assert row["message_age_undated_polls"] == 2


def test_the_share_of_polls_older_than_65_s_is_of_the_polls_measured():
    row = _shape(polls=8, stale_polls=1)
    assert (row["message_age_stale_polls"], row["message_age_stale_pct"]) == (1, 12.5)
    assert _shape(polls=3, stale_polls=1)["message_age_stale_pct"] == 33.33
    assert _shape(stale_polls=0)["message_age_stale_pct"] == 0.0


@pytest.mark.parametrize(
    ("total_rows", "dated_polls", "polls"),
    [(0, 0, 0), (1200, 0, 0), (1200, 3, 0)],
)
def test_without_a_measurable_poll_the_stale_share_is_missing_with_the_message_ages_reason(
    total_rows, dated_polls, polls
):
    row = _shape(total_rows=total_rows, dated_polls=dated_polls, polls=polls, stale_polls=0, ages=None)
    assert (row["message_age_stale_polls"], row["message_age_stale_pct"]) == (None, None)
    assert row["message_age_missing"] is not None


def test_vehicle_positions_are_not_recorded():
    row = _shape()
    assert (row["vehicle_positions_available"], row["vehicle_positions_missing"]) == (None, "not_recorded")


def test_the_month_in_progress_counts_through_the_last_closed_day():
    assert through_of(date(2026, 11, 1), date(2026, 11, 8)) == date(2026, 11, 8)
    assert through_of(_OCT, date(2026, 11, 8)) == date(2026, 10, 31)
    assert through_of(date(2024, 2, 1), date(2024, 11, 8)) == date(2024, 2, 29)


def _spans(windows: dict[date, date]) -> dict[date, tuple[date, date]]:
    """Each month's span when its coverage day is its window's last."""
    return {month: (last, last) for month, last in windows.items()}


def test_a_full_rebuild_builds_every_month_from_the_first_observed_one():
    windows = month_windows(date(2026, 9, 10), date(2026, 11, 8))
    assert windows == {
        date(2026, 9, 1): date(2026, 9, 30),
        _OCT: date(2026, 10, 31),
        date(2026, 11, 1): date(2026, 11, 8),
    }
    assert plan_months(None, _spans(windows), {}) == (None, [date(2026, 9, 1), _OCT, date(2026, 11, 1)])


def test_an_incremental_run_builds_the_changed_months_and_the_ones_whose_window_grew():
    stored = _spans(
        {date(2026, 9, 1): date(2026, 9, 30), _OCT: date(2026, 10, 31), date(2026, 11, 1): date(2026, 11, 7)}
    )
    spans = {**stored, date(2026, 11, 1): (date(2026, 11, 8), date(2026, 11, 8))}
    purge, build = plan_months([date(2026, 9, 14)], spans, stored)
    # September for its changed day, November because the 8th closed;
    # October already counts its whole window.
    assert purge == build == [date(2026, 9, 1), date(2026, 11, 1)]


def test_a_changed_date_in_the_window_past_its_coverage_day_still_builds_the_month():
    """October's trips count through the 20th, the last day any listed agency
    observed in it, but its clamp share counts the whole month, so a change on
    the 25th reaches its row."""
    stored = {_OCT: (date(2026, 10, 20), date(2026, 10, 31))}
    assert plan_months([date(2026, 10, 25)], stored, stored) == ([_OCT], [_OCT])


def test_a_changed_date_past_the_last_counted_day_builds_nothing_while_the_window_stands():
    stored = _spans({_OCT: date(2026, 10, 31), date(2026, 11, 1): date(2026, 11, 8)})
    assert plan_months([date(2026, 11, 9)], stored, stored) == ([], [])


def test_a_window_that_shrank_is_rebuilt_too():
    """A stored month counted further than its window now reaches, as after
    the agency holding the newest day is deleted."""
    stored = _spans({_OCT: date(2026, 10, 31), date(2026, 11, 1): date(2026, 11, 8)})
    spans = {**stored, date(2026, 11, 1): (date(2026, 11, 5), date(2026, 11, 5))}
    assert plan_months([], spans, stored) == ([date(2026, 11, 1)], [date(2026, 11, 1)])


def test_a_window_that_grew_past_an_unmoved_coverage_day_is_rebuilt():
    """The month closed, so its window now reaches its last day, though no
    agency observed a later day of it: its clamp share counts the new days."""
    stored = {_OCT: (date(2026, 10, 28), date(2026, 10, 28))}
    spans = {_OCT: (date(2026, 10, 28), date(2026, 10, 31)), date(2026, 11, 1): (date(2026, 11, 1), date(2026, 11, 1))}
    assert plan_months([], spans, stored) == ([_OCT, date(2026, 11, 1)], [_OCT, date(2026, 11, 1)])


def test_a_new_month_is_built_on_its_first_closed_day_without_a_changed_date():
    stored = _spans({_OCT: date(2026, 10, 31)})
    spans = {**stored, date(2026, 11, 1): (date(2026, 11, 1), date(2026, 11, 1))}
    assert plan_months([], spans, stored) == ([date(2026, 11, 1)], [date(2026, 11, 1)])


def test_a_stored_month_outside_the_observed_months_is_purged_not_built():
    stored = _spans({date(2026, 8, 1): date(2026, 8, 31), _OCT: date(2026, 10, 31)})
    purge, build = plan_months([], _spans({_OCT: date(2026, 10, 31)}), stored)
    assert (purge, build) == ([date(2026, 8, 1)], [])


def test_an_agency_with_no_closed_observed_day_builds_nothing():
    assert month_windows(None, date(2026, 11, 8)) == {}
    assert month_windows(date(2026, 11, 9), date(2026, 11, 8)) == {}
    assert month_windows(date(2026, 10, 1), None) == {}
    assert plan_months(None, {}, {}) == (None, [])
    assert plan_months([], {}, _spans({_OCT: date(2026, 10, 31)})) == ([_OCT], [])


def test_a_month_counts_through_the_newest_day_any_listed_agency_observed_within_it():
    """The board's coverage stops there: a later day of the month no agency
    observed is a lag or an outage every feed shares. A month no agency
    observed counts its whole window, as the board's coverage then does."""
    newest = [date(2026, 10, 3), None, date(2026, 10, 20)]
    assert month_through(date(2026, 10, 31), newest) == date(2026, 10, 20)
    assert month_through(date(2026, 10, 31), [None]) == date(2026, 10, 31)


def test_each_months_bounds_come_from_the_listed_agencies_days_and_its_own():
    """Agency 8, deleted agency 9 and listed agency 10. A deleted agency's
    later day does not move the coverage day, but its own observation is one."""
    windows = {date(2026, 9, 1): date(2026, 9, 30), _OCT: date(2026, 10, 31), date(2026, 11, 1): date(2026, 11, 8)}
    days = [
        (date(2026, 9, 1), 10, True, date(2026, 9, 12)),
        (_OCT, 8, True, date(2026, 10, 3)),
        (_OCT, 9, False, date(2026, 10, 30)),
        (_OCT, 10, True, date(2026, 10, 20)),
    ]
    assert month_bounds(8, windows, days) == {
        date(2026, 9, 1): (date(2026, 9, 12), False),
        _OCT: (date(2026, 10, 20), True),
        date(2026, 11, 1): (date(2026, 11, 8), False),
    }
    assert month_bounds(9, windows, days)[_OCT] == (date(2026, 10, 20), True)


def test_months_count_no_further_than_the_newest_day_any_agencys_aggregates_reach():
    assert last_counted_day(date(2026, 11, 9), date(2026, 11, 2)) == date(2026, 11, 2)
    assert last_counted_day(date(2026, 11, 9), date(2026, 11, 10)) == date(2026, 11, 9)
    assert last_counted_day(date(2026, 11, 9), None) is None


def test_the_render_names_the_gate_rules_it_applies():
    rendered = "\n".join(feed_quality.render())
    assert "def coverage_pct" in rendered and "def clamp_pct" in rendered
    assert "def matched_trips" in rendered and "def coverage_through" in rendered
    assert feed_quality.MONTH_DAYS_SQL in feed_quality.render()
    assert "quantilesExactInclusive(0.5, 0.95)" in rendered


def test_a_poll_is_stale_only_when_its_message_is_older_than_65_s():
    assert feed_quality.STALE_MESSAGE_AGE_SEC == 65
    assert "countIf(age > 65) AS stale_polls" in feed_quality.MESSAGE_AGE_CH_SQL


def test_the_message_age_read_takes_an_archived_polls_instant_from_its_name():
    assert feed_quality.ARCHIVE_POLL_INSTANT_CH in feed_quality.MESSAGE_AGE_CH_SQL
    assert feed_quality.MESSAGE_AGE_CH_SQL in feed_quality.render()
