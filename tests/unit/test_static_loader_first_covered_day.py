"""The first day an incoming timetable covers, where a reload stops saving the
outgoing timetable's planned trips: the earliest calendar.txt start date or
calendar_dates.txt date, by the same coverage rule the planned-trip count uses,
under which a service whose range ends before it starts covers no day."""

from datetime import date

from pipeline.static_loader import _first_covered_day

_FLAGS = [True] * 7


def _calendar(start: date, end: date) -> list:
    return ["ALL", *_FLAGS, start, end]


def test_the_earliest_calendar_start_wins():
    rows = [_calendar(date(2026, 4, 20), date(2026, 10, 14)), _calendar(date(2026, 4, 15), date(2026, 5, 1))]
    assert _first_covered_day(rows, []) == date(2026, 4, 15)


def test_a_calendar_dates_day_of_any_kind_counts():
    rows = [_calendar(date(2026, 4, 20), date(2026, 10, 14))]
    assert _first_covered_day(rows, [["ALL", "20260413", "1"]]) == date(2026, 4, 13)
    # A removal still says something about the day, so the timetable covers it.
    assert _first_covered_day(rows, [["ALL", "20260412", "2"]]) == date(2026, 4, 12)


def test_a_malformed_calendar_dates_day_covers_nothing():
    rows = [_calendar(date(2026, 4, 20), date(2026, 10, 14))]
    malformed = [["ALL", raw, "1"] for raw in ("2026041", "20260231", "２０２６０４０１", "", None, " 20260401")]
    assert _first_covered_day(rows, malformed) == date(2026, 4, 20)


def test_a_service_whose_range_ends_before_it_starts_covers_nothing():
    reversed_range = _calendar(date(2026, 4, 1), date(2026, 3, 1))
    assert _first_covered_day([reversed_range, _calendar(date(2026, 4, 20), date(2026, 10, 14))], []) == date(
        2026, 4, 20
    )
    assert _first_covered_day([reversed_range], []) is None


def test_a_timetable_with_no_calendar_covers_no_day():
    assert _first_covered_day([], []) is None
