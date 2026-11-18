"""The route report card's grading rules (pipeline.route_grade)."""

from datetime import date

import pytest

from pipeline.reports.report_card import history_months, latest_closed_month, month_bounds
from pipeline.route_grade import Target, band, grade_route, truancy_cap


def _high(ewt_min, bunching_pct, delivered=99.0):
    return {"ewt_min": ewt_min, "bunching_pct": bunching_pct, "service_delivered_pct": delivered}


def _low(on_time_pct, early_pct, delivered=99.0):
    return {"on_time_pct": on_time_pct, "early_pct": early_pct, "service_delivered_pct": delivered}


@pytest.mark.parametrize(
    ("metric", "value", "grade"),
    [
        ("ewt_min", 0.5, "A"),
        ("ewt_min", 0.6, "B"),
        ("ewt_min", 2.0, "D"),
        ("ewt_min", 2.1, "F"),
        ("bunching_pct", 5.0, "A"),
        ("bunching_pct", 5.1, "B"),
        ("bunching_pct", 20.0, "D"),
        ("bunching_pct", 20.1, "F"),
        ("on_time_pct", 90.0, "A"),
        ("on_time_pct", 89.9, "B"),
        ("on_time_pct", 60.0, "D"),
        ("on_time_pct", 59.9, "F"),
        ("early_pct", 1.0, "A"),
        ("early_pct", 10.0, "D"),
        ("early_pct", 10.1, "F"),
    ],
)
def test_every_edge_is_inclusive(metric, value, grade):
    assert band(metric, value) == grade


def test_a_figure_is_judged_as_printed_to_a_tenth():
    # 5.04 prints as 5.0, at the A edge; 5.05 prints as 5.1, past it.
    assert band("bunching_pct", 5.04) == "A"
    assert band("bunching_pct", 5.05) == "B"


def test_a_high_frequency_route_takes_the_worse_of_its_wait_and_bunching():
    assert grade_route("high", _high(0.4, 9.0), {}).grade == "B"
    assert grade_route("high", _high(1.2, 3.0), {}).grade == "C"


def test_an_f_on_either_high_frequency_figure_is_an_f():
    assert grade_route("high", _high(0.1, 25.0), {}).grade == "F"
    assert grade_route("high", _high(2.5, 1.0), {}).grade == "F"


def test_a_low_frequency_route_takes_the_worse_of_on_time_and_early_departures():
    assert grade_route("low", _low(92.0, 0.5), {}).grade == "A"
    assert grade_route("low", _low(92.0, 4.0), {}).grade == "C"
    assert grade_route("low", _low(65.0, 0.5), {}).grade == "D"


def test_truancy_caps_the_grade_at_d_and_fails_it_further_down():
    assert truancy_cap(95.0) == "A"
    assert truancy_cap(94.9) == "D"
    assert truancy_cap(90.0) == "D"
    assert truancy_cap(89.9) == "F"
    assert grade_route("low", _low(95.0, 0.5, delivered=94.0), {}).grade == "D"
    assert grade_route("high", _high(0.2, 2.0, delivered=85.0), {}).grade == "F"
    # A worse letter than the cap stands.
    assert grade_route("low", _low(50.0, 0.5, delivered=94.0), {}).grade == "F"


def test_a_feed_with_no_service_delivered_rate_is_not_judged_rather_than_passed():
    verdict = grade_route(
        "low", _low(95.0, 0.5, delivered=None), {"service_delivered_pct": "feed_reports_no_cancellations"}
    )
    assert verdict.grade is None
    assert verdict.missing == (("service_delivered_pct", "feed_reports_no_cancellations"),)
    # The figures it has are still lettered.
    assert verdict.letters["on_time_pct"] == "A"


def test_unknown_bunching_leaves_the_route_ungraded_never_read_as_zero():
    verdict = grade_route("high", _high(0.2, None), {})
    assert verdict.grade is None
    assert verdict.missing == (("bunching_pct", "not_measured"),)


def test_what_would_raise_it_names_each_figure_below_the_next_letter():
    assert grade_route("high", _high(0.4, 9.0), {}).raise_to == (Target("bunching_pct", 9.0, 5.0),)
    both = grade_route("high", _high(1.7, 17.0), {})
    assert both.grade == "D"
    assert both.raise_to == (Target("ewt_min", 1.7, 1.5), Target("bunching_pct", 17.0, 15.0))


def test_when_truancy_binds_the_next_letter_needs_the_delivered_rate():
    capped = grade_route("low", _low(95.0, 0.5, delivered=93.0), {})
    assert capped.raise_to == (Target("service_delivered_pct", 93.0, 95.0),)
    failed = grade_route("low", _low(95.0, 0.5, delivered=88.0), {})
    assert failed.grade == "F"
    assert failed.raise_to == (Target("service_delivered_pct", 88.0, 90.0),)


def test_an_a_has_nothing_to_raise():
    assert grade_route("low", _low(95.0, 0.5), {}).raise_to == ()


def test_the_newest_closed_month_is_the_last_one_whose_every_day_has_closed():
    assert latest_closed_month(date(2026, 11, 16)) == date(2026, 10, 1)
    assert latest_closed_month(date(2026, 10, 31)) == date(2026, 10, 1)
    assert latest_closed_month(date(2026, 1, 5)) == date(2025, 12, 1)


def test_a_cards_history_is_its_month_and_the_two_before_it():
    assert history_months(date(2026, 2, 1)) == [date(2025, 12, 1), date(2026, 1, 1), date(2026, 2, 1)]
    assert month_bounds(date(2028, 2, 1)) == (date(2028, 2, 1), date(2028, 2, 29))


def test_every_figure_given_gets_its_letter_graded_on_or_not():
    verdict = grade_route("high", {**_high(0.4, 9.0), "on_time_pct": 85.0, "early_pct": None}, {})
    assert verdict.letters == {"ewt_min": "A", "bunching_pct": "B", "service_delivered_pct": "A", "on_time_pct": "B"}
    # The on-time letter is shown, not graded on.
    assert verdict.grade == "B"
