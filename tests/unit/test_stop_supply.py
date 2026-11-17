"""Planned stop supply: hours, gaps, first and last departures, and the
typical day each day type is read on."""

from datetime import date, timedelta

import pytest

from pipeline.day_types import ServiceCalendar, WeeklyService
from pipeline.reports.stop_supply import version_on
from pipeline.stop_supply import (
    BandSupply,
    HourCell,
    band_supply,
    hour_cells,
    hourly_rows,
    typical_days,
    versions_in_force,
)

H = 3600
M = 60


def hm(hour: int, minute: int = 0) -> int:
    return hour * H + minute * M


def test_departures_bucket_by_hour_with_first_last_and_inner_gap():
    cells = hour_cells([hm(10, 50), hm(10, 5), hm(10, 20), hm(11, 0)])
    assert cells == {
        10: HourCell(3, hm(10, 5), hm(10, 50), 30 * M),
        11: HourCell(1, hm(11), hm(11), None),
    }


def test_an_after_midnight_time_written_at_face_value_is_the_same_nights():
    cells = hour_cells([hm(0, 10), hm(23, 50)])
    assert cells == {23: HourCell(1, hm(23, 50), hm(23, 50), None), 24: HourCell(1, hm(24, 10), hm(24, 10), None)}


def test_an_extended_hour_and_its_face_value_land_in_the_same_hour():
    assert hour_cells([hm(24, 10), hm(0, 40)]) == {24: HourCell(2, hm(24, 10), hm(24, 40), 30 * M)}


def test_the_cutover_hour_is_the_morning_and_the_hour_before_it_the_night():
    cells = hour_cells([hm(4, 0), hm(3, 59)])
    assert set(cells) == {4, 27}


def test_two_departures_at_one_time_wait_nothing_between_them():
    assert hour_cells([hm(8), hm(8)]) == {8: HourCell(2, hm(8), hm(8), 0)}


def test_a_band_with_no_departure_is_one_gap_the_whole_band():
    cells = hour_cells([hm(8), hm(17)])
    assert band_supply(cells, 10, 16) == BandSupply(0, 0.0, 6 * H)


def test_one_departure_splits_the_band_at_it():
    assert band_supply(hour_cells([hm(12)]), 10, 16).longest_gap_sec == 4 * H


def test_a_gap_across_an_hour_boundary_counts_whole():
    cells = hour_cells(
        [hm(10), hm(10, 20), hm(10, 50), hm(11, 40), *(hm(h, m) for h in range(12, 16) for m in (0, 30)), hm(15, 59)]
    )
    assert band_supply(cells, 10, 16).longest_gap_sec == 50 * M


def test_a_gap_across_the_band_start_counts_only_inside_the_band():
    cells = hour_cells([hm(9, 30), hm(10, 40), *(hm(h, m) for h in range(11, 16) for m in (0, 30)), hm(15, 59)])
    assert band_supply(cells, 10, 16).longest_gap_sec == 40 * M


def test_a_gap_across_the_band_end_counts_only_inside_the_band():
    cells = hour_cells([*(hm(h, m) for h in range(10, 15) for m in (0, 30)), hm(15), hm(16, 40)])
    assert band_supply(cells, 10, 16).longest_gap_sec == 60 * M


def test_service_ending_inside_the_band_leaves_the_rest_a_gap():
    cells = hour_cells([hm(h, m) for h in range(6, 14) for m in (0, 30)] + [hm(14)])
    assert band_supply(cells, 10, 16).longest_gap_sec == 2 * H


def test_service_starting_inside_the_band_counts_from_its_start():
    cells = hour_cells([hm(11, 30), hm(12), hm(12, 30), hm(13), hm(14), hm(15), hm(15, 59)])
    assert band_supply(cells, 10, 16).longest_gap_sec == 90 * M


def test_the_inner_gap_of_an_hour_can_be_the_longest():
    cells = hour_cells([hm(10), hm(10, 1), hm(10, 59), hm(11, 0)])
    assert band_supply(cells, 10, 11).longest_gap_sec == 58 * M


def test_departures_per_hour_are_the_bands_count_over_its_hours():
    cells = hour_cells([hm(h, m) for h in range(6, 22) for m in (0, 15, 30, 45)])
    supply = band_supply(cells, 10, 16)
    assert (supply.departures, supply.per_hour) == (24, 4.0)


def test_a_night_band_reads_after_midnight_departures():
    cells = hour_cells([hm(23, 30), hm(0, 15)])
    assert band_supply(cells, 23, 25) == BandSupply(2, 1.0, 45 * M)


def test_an_empty_band_is_refused():
    with pytest.raises(ValueError):
        band_supply({}, 10, 10)


MON = date(2026, 6, 1)  # a Monday, no public holiday within the next weeks
WEEKDAYS = (True, True, True, True, True, False, False)
SATURDAY = (False, False, False, False, False, True, False)
SUNDAY = (False, False, False, False, False, False, True)


def _calendar(weekly, exceptions=()):
    return ServiceCalendar.of(weekly, list(exceptions))


def test_each_day_type_reads_the_services_its_dates_run():
    calendar = _calendar(
        [
            WeeklyService("wk", WEEKDAYS, MON, MON + timedelta(days=200)),
            WeeklyService("sa", SATURDAY, MON, MON + timedelta(days=200)),
            WeeklyService("su", SUNDAY, MON, MON + timedelta(days=200)),
        ]
    )
    days = typical_days([(MON, MON + timedelta(days=200))], calendar)
    assert {dt: (d.day, d.services) for dt, d in days.items() if d} == {
        "weekday": (MON, frozenset({"wk"})),
        "saturday": (MON + timedelta(days=5), frozenset({"sa"})),
        "sunday_holiday": (MON + timedelta(days=6), frozenset({"su"})),
    }


def test_a_one_off_weekday_does_not_stand_for_the_weekday():
    calendar = _calendar(
        [WeeklyService("wk", WEEKDAYS, MON, MON + timedelta(days=200))],
        [("special", MON, 1)],
    )
    weekday = typical_days([(MON, MON + timedelta(days=200))], calendar)["weekday"]
    assert weekday is not None and weekday.services == frozenset({"wk"})
    assert weekday.day == MON + timedelta(days=1)


def test_a_public_holiday_on_a_weekday_reads_as_sunday_holiday_not_weekday():
    # 2026-07-20 is Marine Day, a Monday; the version is in force that week only.
    holiday = date(2026, 7, 20)
    calendar = _calendar(
        [WeeklyService("wk", WEEKDAYS, holiday, holiday + timedelta(days=60))],
        [("wk", holiday, 2), ("hol", holiday, 1)],
    )
    days = typical_days([(holiday, holiday + timedelta(days=4))], calendar)
    assert days["weekday"] is not None and days["sunday_holiday"] is not None
    assert (days["weekday"].day, days["weekday"].services) == (date(2026, 7, 21), frozenset({"wk"}))
    assert (days["sunday_holiday"].day, days["sunday_holiday"].services) == (holiday, frozenset({"hol"}))


def test_a_day_type_nothing_runs_on_is_known_and_empty():
    calendar = _calendar([WeeklyService("wk", WEEKDAYS, MON, MON + timedelta(days=60))])
    sunday = typical_days([(MON, MON + timedelta(days=60))], calendar)["sunday_holiday"]
    assert sunday is not None and sunday.services == frozenset()


def test_dates_the_version_is_not_in_force_on_are_not_read():
    calendar = _calendar(
        [
            WeeklyService("old", WEEKDAYS, MON, MON + timedelta(days=13)),
            WeeklyService("new", WEEKDAYS, MON + timedelta(days=14), MON + timedelta(days=200)),
        ]
    )
    weekday = typical_days([(MON + timedelta(days=14), MON + timedelta(days=200))], calendar)["weekday"]
    assert weekday is not None and weekday.services == frozenset({"new"})


def test_a_day_type_with_no_date_in_force_is_unknown():
    calendar = _calendar([WeeklyService("wk", WEEKDAYS, MON, MON + timedelta(days=60))])
    days = typical_days([(MON, MON + timedelta(days=4))], calendar)
    assert days["weekday"] is not None
    assert days["saturday"] is None and days["sunday_holiday"] is None


def test_dates_outside_the_calendar_are_unknown_not_empty():
    calendar = _calendar([WeeklyService("wk", WEEKDAYS, MON, MON + timedelta(days=4))])
    days = typical_days([(MON, MON + timedelta(days=60))], calendar)
    assert days["saturday"] is None


def test_a_calendar_with_no_rows_tells_no_day():
    assert set(typical_days([(MON, MON + timedelta(days=60))], _calendar([])).values()) == {None}


def test_a_version_in_force_twice_is_one_version_with_two_runs():
    a1, a2, b = (
        (MON, MON + timedelta(days=9)),
        (MON + timedelta(days=20), MON + timedelta(days=30)),
        (
            MON + timedelta(days=10),
            MON + timedelta(days=19),
        ),
    )
    versions = versions_in_force([(*a1, "A", "ca"), (*b, "B", "cb"), (*a2, "A", "ca")])
    assert {(v.stem, v.content): v.segments for v in versions} == {("A", "ca"): (a1, a2), ("B", "cb"): (b,)}


def test_the_version_on_a_date_is_the_segment_covering_it():
    segments = [
        (MON, MON + timedelta(days=9), "A", "ca"),
        (MON + timedelta(days=10), MON + timedelta(days=30), "B", "cb"),
    ]
    assert version_on(segments, MON + timedelta(days=10)) == ("B", "cb")
    assert version_on(segments, MON - timedelta(days=1)) is None


def test_rows_carry_their_day_type_in_day_type_order():
    rows = hourly_rows(7, "S", "c", [(1, "s1", [hm(10)]), (3, "s1", [hm(0, 30)])])
    assert rows == [
        (7, "S", "c", "weekday", "s1", 10, 1, hm(10), hm(10), None),
        (7, "S", "c", "sunday_holiday", "s1", 24, 1, hm(24, 30), hm(24, 30), None),
    ]
