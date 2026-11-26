"""Planned stop supply: hours, gaps, first and last departures, and the
typical day each day type is read on."""

from datetime import date, timedelta
from decimal import Decimal

import pytest

from pipeline.day_types import ServiceCalendar, WeeklyService
from pipeline.reports.stop_supply import ObservedHour, hour_rows, matching_days, version_on
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


def test_after_midnight_departures_are_hours_24_and_later():
    # Service-day seconds: the departures SQL has already moved a face-value
    # 00:10 to 24:10 (pipeline.timetable_sampling).
    cells = hour_cells([hm(24, 10), hm(23, 50), hm(27, 59)])
    assert cells == {
        23: HourCell(1, hm(23, 50), hm(23, 50), None),
        24: HourCell(1, hm(24, 10), hm(24, 10), None),
        27: HourCell(1, hm(27, 59), hm(27, 59), None),
    }


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
    cells = hour_cells([hm(23, 30), hm(24, 15)])
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
    rows = hourly_rows(7, "S", "c", [(1, "s1", [hm(10)]), (3, "s1", [hm(24, 30)])])
    assert rows == [
        (7, "S", "c", "weekday", "s1", 10, 1, hm(10), hm(10), None),
        (7, "S", "c", "sunday_holiday", "s1", 24, 1, hm(24, 30), hm(24, 30), None),
    ]


def test_the_tables_fingerprint_what_decides_their_rows():
    import inspect

    from pipeline import stop_supply, timetable_sampling

    days = stop_supply.render_days()
    for shared in (
        timetable_sampling.typical_days,
        timetable_sampling.typical_day,
        timetable_sampling.service_calendar,
    ):
        assert inspect.getsource(shared) in days
    hourly = stop_supply.render()
    assert timetable_sampling.DEPARTURE_SECONDS_SQL in hourly
    assert inspect.getsource(timetable_sampling.services_by_day_type) in hourly
    assert not set(days) & set(hourly)


def test_a_versions_dates_in_force_read_as_one_digest():
    from pipeline.stop_supply import segments_digest

    assert segments_digest([(MON, MON + timedelta(days=9)), (MON + timedelta(days=20), MON + timedelta(days=30))]) == (
        "2026-06-01/2026-06-10,2026-06-21/2026-07-01"
    )


_A, _B = ("a", "ha"), ("b", "hb")
_SEGMENTS = [(date(2026, 11, 1), date(2026, 11, 16), *_A), (date(2026, 11, 17), date(2026, 12, 31), *_B)]


def test_observed_days_are_the_versions_dates_of_the_day_type():
    # 11/23 is a Monday and a public holiday; 11/16 runs the other version.
    dates = [date(2026, 11, d) for d in (14, 15, 16, 17, 18, 21, 22, 23, 24)]
    assert matching_days(dates, _SEGMENTS, _B, "weekday") == [date(2026, 11, d) for d in (17, 18, 24)]
    assert matching_days(dates, _SEGMENTS, _B, "saturday") == [date(2026, 11, 21)]
    assert matching_days(dates, _SEGMENTS, _B, "sunday_holiday") == [date(2026, 11, 22), date(2026, 11, 23)]
    assert matching_days(dates, _SEGMENTS, _A, "weekday") == [date(2026, 11, 16)]
    assert matching_days([], _SEGMENTS, _B, "weekday") == []


def test_an_hour_sets_its_observed_departures_beside_the_planned_ones():
    planned = {8: HourCell(4, hm(8, 5), hm(8, 50), hm(0, 15)), 9: HourCell(2, hm(9, 10), hm(9, 40), hm(0, 30))}
    # Over 4 days: 12 departures observed at 8, 3 over five minutes late and 1
    # over a minute early, 2,232 s late in all. None at 9; 2 at 10, which the
    # timetable does not plan.
    observed = {8: ObservedHour(12, 2232, 7, 3, 1), 10: ObservedHour(2, -30, 2, 0, 0)}
    rows = hour_rows(planned, observed, 4, 7, 11)
    assert rows == [
        {
            "hour": 8,
            "departures": 4,
            "observed": {
                "departures": 12,
                "per_day": Decimal("3.0"),
                "on_time": 8,
                "on_time_pct": Decimal("66.7"),
                "uncertain": True,
                "avg_delay_min": Decimal("3.1"),
            },
        },
        {
            "hour": 9,
            "departures": 2,
            "observed": {
                "departures": 0,
                "per_day": Decimal("0.0"),
                "on_time": 0,
                "on_time_pct": None,
                "uncertain": True,
                "avg_delay_min": None,
            },
        },
        {
            "hour": 10,
            "departures": 0,
            "observed": {
                "departures": 2,
                "per_day": Decimal("0.5"),
                "on_time": 2,
                "on_time_pct": Decimal("100.0"),
                "uncertain": True,
                "avg_delay_min": Decimal("-0.3"),
            },
        },
    ]


def test_without_an_observed_window_an_hour_carries_only_its_plan():
    planned = {8: HourCell(4, hm(8, 5), hm(8, 50), hm(0, 15)), 12: HourCell(1, hm(12), hm(12), None)}
    assert hour_rows(planned, None, 0, 7, 12) == [{"hour": 8, "departures": 4, "observed": None}]


def test_a_well_backed_percentage_is_not_uncertain_and_halves_round_up():
    # 1,000 departures, 100 over five minutes late and 25 over a minute
    # early, so 875 on time: 87.5 %. A mean of 0.25 min rounds up to 0.3.
    observed = {8: ObservedHour(1000, 15000, 700, 100, 25)}
    (row,) = hour_rows({}, observed, 8, 8, 9)
    assert row["observed"]["on_time_pct"] == Decimal("87.5")
    assert row["observed"]["uncertain"] is False
    assert row["observed"]["avg_delay_min"] == Decimal("0.3")
    assert row["observed"]["per_day"] == Decimal("125.0")
