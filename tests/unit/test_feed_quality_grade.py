"""The feed-quality scorecard's monthly grade, from a month's stored figures alone."""

import pytest

from pipeline import feed_quality_grade
from pipeline.feed_quality_grade import CRITERIA, GRADES, grade_month
from pipeline.reports.comparability import MAX_CLAMP_PCT


def _month(**over):
    """A month every criterion passes, as the admin drawer reads it."""
    row = {
        "rt_completeness_pct": 98.6,
        "rt_completeness_missing": None,
        "stop_completeness_pct": None,
        "stop_completeness_missing": "no_stop_plan",
        "message_age_polls": 34144,
        "message_age_stale_polls": 410,
        "message_age_stale_pct": 1.2,
        "message_age_p50_sec": 20.0,
        "message_age_p95_sec": 37.0,
        "message_age_missing": None,
        "clamp_pct": 0.3,
        "clamp_missing": None,
        "stop_time_updates": 1200,
        "stop_id_pct": 100.0,
        "arr_delay_pct": 12.5,
        "schedule_relationship_trip_pct": 100.0,
        "schedule_relationship_stop_pct": 100.0,
        "field_coverage_missing": None,
    }
    return {**row, **over}


def test_the_thresholds_start_from_cal_itps_and_the_gates():
    assert feed_quality_grade.MIN_RT_COMPLETENESS_PCT == 95.0
    assert feed_quality_grade.MAX_STALE_POLLS_PCT == 5.0
    assert MAX_CLAMP_PCT == 5.0
    assert CRITERIA == ("rt_completeness", "stale_polls", "clamp", "field_coverage")
    assert GRADES == ("A", "B", "C", "D", "F")


def test_a_month_passing_every_criterion_is_an_a():
    graded = grade_month(_month())
    assert (graded.grade, graded.failing, graded.missing) == ("A", (), ())


@pytest.mark.parametrize(
    ("over", "criterion"),
    [
        ({"rt_completeness_pct": 94.9}, "rt_completeness"),
        ({"message_age_stale_pct": 5.01}, "stale_polls"),
        ({"clamp_pct": 5.05}, "clamp"),
        ({"stop_id_pct": 98.9}, "field_coverage"),
    ],
)
def test_each_failing_criterion_costs_one_grade(over, criterion):
    graded = grade_month(_month(**over))
    assert (graded.grade, graded.failing, graded.missing) == ("B", (criterion,), ())


@pytest.mark.parametrize(
    "over",
    [
        {"rt_completeness_pct": 95.0},
        {"rt_completeness_pct": 110.1},
        {"message_age_stale_pct": 5.0},
        {"clamp_pct": 5.04},
        {"stop_id_pct": 99.0},
    ],
)
def test_a_figure_at_its_threshold_as_printed_passes(over):
    """Each threshold is inclusive. Trips reported beyond the plan are the
    feed naming trips the plan does not, not a shortfall. The clamp share is
    judged to a tenth, as the comparability gate judges it."""
    assert grade_month(_month(**over)).grade == "A"


@pytest.mark.parametrize(
    "over",
    [
        {"arr_delay_pct": 0.0},
        {"arr_delay_pct": 50.0},
        {"schedule_relationship_trip_pct": 98.9},
        {"schedule_relationship_stop_pct": 98.9},
    ],
)
def test_field_coverage_passes_only_as_the_rt_field_probe_would_confirm_it(over):
    """arr_delay is the sparse arrival estimate the app reads it as only when
    some but fewer than half of the updates carry it."""
    assert grade_month(_month(**over)).failing == ("field_coverage",)


@pytest.mark.parametrize(
    ("over", "grade"),
    [
        ({"rt_completeness_pct": 80.0, "clamp_pct": 7.0}, "C"),
        ({"rt_completeness_pct": 80.0, "clamp_pct": 7.0, "message_age_stale_pct": 9.0}, "D"),
        ({"rt_completeness_pct": 80.0, "clamp_pct": 7.0, "message_age_stale_pct": 9.0, "arr_delay_pct": 0.0}, "F"),
    ],
)
def test_the_grade_counts_the_failing_criteria(over, grade):
    graded = grade_month(_month(**over))
    assert graded.grade == grade
    assert len(graded.failing) == GRADES.index(grade)


def test_a_month_missing_a_needed_figure_has_no_grade_and_says_why():
    graded = grade_month(
        _month(
            rt_completeness_pct=None,
            rt_completeness_missing="coverage_unknown",
            stop_id_pct=None,
            arr_delay_pct=None,
            schedule_relationship_trip_pct=None,
            schedule_relationship_stop_pct=None,
            field_coverage_missing="feed_cannot_report",
            clamp_pct=7.0,
        )
    )
    assert graded.grade is None
    assert graded.missing == (("rt_completeness", "coverage_unknown"), ("field_coverage", "feed_cannot_report"))
    # What the stated figures fail is still named.
    assert graded.failing == ("clamp",)


@pytest.mark.parametrize(
    ("over", "missing"),
    [
        (
            {"message_age_stale_pct": None, "message_age_p50_sec": None, "message_age_missing": "archive_stamped"},
            ("stale_polls", "archive_stamped"),
        ),
        ({"clamp_pct": None, "clamp_missing": "no_data"}, ("clamp", "no_data")),
        # A month built before the share was stored says it is not recorded,
        # never reads as 0.
        ({"message_age_stale_pct": None, "message_age_stale_polls": None}, ("stale_polls", "not_recorded")),
    ],
)
def test_each_criterion_without_its_figure_takes_the_scorecards_reason(over, missing):
    graded = grade_month(_month(**over))
    assert (graded.grade, graded.missing) == (None, (missing,))


def test_figures_outside_the_criteria_never_decide_the_grade():
    graded = grade_month(
        _month(
            stop_completeness_pct=12.0,
            stop_completeness_missing=None,
            message_age_p50_sec=3600.0,
            message_age_p95_sec=52200.0,
        )
    )
    assert graded.grade == "A"
