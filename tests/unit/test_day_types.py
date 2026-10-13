from datetime import date, timedelta

import pytest

from pipeline.day_types import (
    THREE_WAY,
    TWO_WAY,
    WeeklyService,
    fallback_day_type,
    public_holidays_between,
    resolve_day_types,
    service_class,
)

WEEKDAYS = (True, True, True, True, True, False, False)
SATURDAY = (False, False, False, False, False, True, False)
SUNDAY = (False, False, False, False, False, False, True)
WEEKEND = (False, False, False, False, False, True, True)

CAL_START, CAL_END = date(2026, 8, 1), date(2026, 10, 31)


def _days(lo: date, hi: date) -> list[date]:
    return [lo + timedelta(days=i) for i in range((hi - lo).days + 1)]


# ---------------------------------------------------------------------------
# The weekday rule, used where the feed's calendar is silent.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("day", "expected"),
    [
        (date(2026, 9, 16), "weekday"),  # an ordinary Wednesday
        (date(2026, 9, 19), "saturday"),
        (date(2026, 9, 20), "sunday_holiday"),
        # Public holidays on a weekday run the holiday timetable.
        (date(2026, 9, 21), "sunday_holiday"),  # Respect for the Aged Day, a Monday
        (date(2026, 9, 22), "sunday_holiday"),  # citizens' holiday between two holidays
        (date(2026, 5, 6), "sunday_holiday"),  # substitute holiday for Sunday 5/3
        # A public holiday on a Saturday is a holiday; the year-end break is not one.
        (date(2025, 5, 3), "sunday_holiday"),  # Constitution Memorial Day, a Saturday
        (date(2026, 1, 3), "saturday"),
    ],
)
def test_fallback_reads_the_weekday_and_the_public_holiday_calendar(day, expected):
    assert fallback_day_type(day) == expected


def test_fallback_matches_the_holiday_list_it_is_given():
    holidays = frozenset(public_holidays_between(date(2026, 9, 14), date(2026, 9, 27)))
    assert holidays == {date(2026, 9, 21), date(2026, 9, 22), date(2026, 9, 23)}
    for d in _days(date(2026, 9, 14), date(2026, 9, 27)):
        assert fallback_day_type(d, holidays) == fallback_day_type(d)


# ---------------------------------------------------------------------------
# A service's class comes from the weekdays its calendar.txt row names.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("flags", "expected"),
    [
        (WEEKDAYS, "weekday"),
        ((True, True, True, True, False, False, False), "weekday"),  # Mon-Thu, with Friday its own service
        ((False, False, False, False, True, False, False), "weekday"),
        (SATURDAY, "saturday"),
        (SUNDAY, "sunday_holiday"),
        (WEEKEND, "weekend_holiday"),
        ((True, True, True, True, True, True, False), None),  # Mon-Sat mixes two classes
        ((True,) * 7, None),
        ((False,) * 7, None),  # runs only on dates calendar_dates.txt adds
    ],
)
def test_service_class_reads_the_weekly_flags(flags, expected):
    assert service_class(flags) == expected


# ---------------------------------------------------------------------------
# A date's day type is the class of the services the calendar runs that date.
# ---------------------------------------------------------------------------


def _hiroshima_like():
    """Three timetable classes, as the Hiroshima feeds publish them."""
    return [
        WeeklyService("11_平日(共通)", WEEKDAYS, CAL_START, CAL_END),
        WeeklyService("11_土曜(共通)", SATURDAY, CAL_START, CAL_END),
        WeeklyService("11_日祝(共通)", SUNDAY, CAL_START, CAL_END),
    ]


TRIPS = {"11_平日(共通)": 300, "11_土曜(共通)": 200, "11_日祝(共通)": 150}


def test_a_three_class_feed_gets_three_buckets_and_runs_its_calendar():
    holiday_swap = [("11_日祝(共通)", date(2026, 9, 21), 1), ("11_平日(共通)", date(2026, 9, 21), 2)]
    types = resolve_day_types(date(2026, 9, 14), date(2026, 9, 21), _hiroshima_like(), holiday_swap, TRIPS)
    assert types.buckets == THREE_WAY
    assert [types.of(d) for d in _days(date(2026, 9, 14), date(2026, 9, 21))] == [
        *["weekday"] * 5,
        "saturday",
        "sunday_holiday",
        "sunday_holiday",  # Monday 9/21 runs the 日祝 timetable the feed swapped in
    ]


def test_a_saturday_the_feed_runs_on_its_holiday_timetable_counts_as_a_holiday():
    swap = [("11_日祝(共通)", date(2026, 8, 15), 1), ("11_土曜(共通)", date(2026, 8, 15), 2)]
    types = resolve_day_types(date(2026, 8, 10), date(2026, 8, 16), _hiroshima_like(), swap, TRIPS)
    assert date(2026, 8, 15).isoweekday() == 6
    assert types.of(date(2026, 8, 15)) == "sunday_holiday"


def test_a_weekday_running_the_saturday_timetable_counts_as_a_saturday():
    swap = [("11_土曜(共通)", date(2026, 10, 29), 1), ("11_平日(共通)", date(2026, 10, 29), 2)]
    types = resolve_day_types(date(2026, 10, 26), date(2026, 10, 30), _hiroshima_like(), swap, TRIPS)
    assert types.of(date(2026, 10, 29)) == "saturday"


def test_an_aomori_like_two_class_feed_gets_two_buckets():
    """A feed whose only weekend timetable runs Saturday and Sunday alike has no
    Saturday to tell apart from Sunday: inventing a 土曜 vs 日祝 gap from one
    timetable would mislead, so it gets 平日 / 土日祝."""
    services = [
        WeeklyService("平日", WEEKDAYS, CAL_START, CAL_END),
        WeeklyService("土日祝", WEEKEND, CAL_START, CAL_END),
    ]
    swap = [("土日祝", date(2026, 9, 21), 1), ("平日", date(2026, 9, 21), 2)]
    types = resolve_day_types(date(2026, 9, 14), date(2026, 9, 21), services, swap, {"平日": 834, "土日祝": 710})
    assert types.buckets == TWO_WAY
    assert [types.of(d) for d in _days(date(2026, 9, 18), date(2026, 9, 21))] == [
        "weekday",
        "weekend_holiday",
        "weekend_holiday",
        "weekend_holiday",
    ]
    # A window of weekdays alone still belongs to a two-class feed.
    weekdays_only = resolve_day_types(date(2026, 9, 14), date(2026, 9, 18), services, [], {})
    assert weekdays_only.buckets == TWO_WAY


def test_a_two_class_feed_folds_fallback_weekends_into_its_weekend_bucket():
    services = [
        WeeklyService("平日", WEEKDAYS, CAL_START, date(2026, 9, 18)),
        WeeklyService("土日祝", WEEKEND, CAL_START, date(2026, 9, 18)),
    ]
    types = resolve_day_types(date(2026, 9, 14), date(2026, 9, 21), services, [], {})
    assert types.buckets == TWO_WAY
    assert types.of(date(2026, 9, 19)) == "weekend_holiday"  # past end_date: Saturday by the weekday rule
    assert types.of(date(2026, 9, 21)) == "weekend_holiday"  # past end_date: a public holiday


def test_dates_the_calendar_does_not_cover_fall_back_to_the_weekday_rule():
    services = [WeeklyService(s.service_id, s.days, CAL_START, date(2026, 9, 15)) for s in _hiroshima_like()]
    types = resolve_day_types(date(2026, 9, 14), date(2026, 9, 21), services, [], TRIPS)
    assert types.buckets == THREE_WAY
    assert types.of(date(2026, 9, 19)) == "saturday"
    assert types.of(date(2026, 9, 21)) == "sunday_holiday"  # a holiday by jpholiday, not by any calendar row
    no_calendar = resolve_day_types(date(2026, 9, 14), date(2026, 9, 21), [], [], {})
    assert no_calendar.buckets == THREE_WAY
    assert [no_calendar.of(d) for d in _days(date(2026, 9, 18), date(2026, 9, 21))] == [
        "weekday",
        "saturday",
        "sunday_holiday",
        "sunday_holiday",
    ]


def test_a_mixed_date_takes_the_class_running_the_most_trips():
    services = [*_hiroshima_like(), WeeklyService("12_日祝(共通)", SUNDAY, CAL_START, CAL_END)]
    # Saturday 9/19: route group 12 alone runs its holiday timetable.
    swap = [("12_日祝(共通)", date(2026, 9, 19), 1)]
    small = resolve_day_types(date(2026, 9, 19), date(2026, 9, 19), services, swap, {**TRIPS, "12_日祝(共通)": 40})
    assert small.of(date(2026, 9, 19)) == "saturday"
    big = resolve_day_types(date(2026, 9, 19), date(2026, 9, 19), services, swap, {**TRIPS, "12_日祝(共通)": 500})
    assert big.of(date(2026, 9, 19)) == "sunday_holiday"


def test_without_trip_counts_each_service_counts_once():
    services = [*_hiroshima_like(), WeeklyService("12_日祝(共通)", SUNDAY, CAL_START, CAL_END)]
    swap = [("12_日祝(共通)", date(2026, 9, 19), 1), ("11_日祝(共通)", date(2026, 9, 19), 1)]
    types = resolve_day_types(date(2026, 9, 19), date(2026, 9, 19), services, swap, {})
    assert types.of(date(2026, 9, 19)) == "sunday_holiday"


def test_a_tie_goes_to_the_weekday_rule():
    services = [*_hiroshima_like()]
    swap = [("11_日祝(共通)", date(2026, 9, 19), 1)]
    trips = {"11_土曜(共通)": 100, "11_日祝(共通)": 100}
    assert resolve_day_types(date(2026, 9, 19), date(2026, 9, 19), services, swap, trips).of(date(2026, 9, 19)) == (
        "saturday"
    )


def test_services_without_a_weekly_class_do_not_vote():
    """One-off services (calendar_dates only, or flags spanning two classes)
    say nothing about which timetable the day runs."""
    services = [*_hiroshima_like(), WeeklyService("MON_SAT", (True,) * 6 + (False,), CAL_START, CAL_END)]
    swap = [("4ac647ac-uuid", date(2026, 9, 19), 1), ("お盆臨時", date(2026, 9, 16), 1)]
    trips = {**TRIPS, "MON_SAT": 10_000, "4ac647ac-uuid": 10_000, "お盆臨時": 10_000}
    types = resolve_day_types(date(2026, 9, 14), date(2026, 9, 20), services, swap, trips)
    assert types.of(date(2026, 9, 16)) == "weekday"
    assert types.of(date(2026, 9, 19)) == "saturday"


def test_a_three_class_feed_splits_a_weekend_service_by_date():
    services = [*_hiroshima_like(), WeeklyService("53_土日祝", WEEKEND, CAL_START, CAL_END)]
    trips = {**TRIPS, "53_土日祝": 1_000}
    types = resolve_day_types(
        date(2026, 9, 19), date(2026, 9, 21), services, [("53_土日祝", date(2026, 9, 21), 1)], trips
    )
    assert types.buckets == THREE_WAY
    assert [types.of(d) for d in _days(date(2026, 9, 19), date(2026, 9, 21))] == [
        "saturday",
        "sunday_holiday",
        "sunday_holiday",
    ]


def test_a_removal_wins_over_the_weekly_flags():
    swap = [("11_平日(共通)", date(2026, 9, 16), 2)]
    types = resolve_day_types(date(2026, 9, 16), date(2026, 9, 16), _hiroshima_like(), swap, TRIPS)
    # Nothing classed runs that Wednesday, so the weekday rule decides.
    assert types.of(date(2026, 9, 16)) == "weekday"
