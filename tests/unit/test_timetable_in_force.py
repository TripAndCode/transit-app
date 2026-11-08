"""Which timetable is in force on a service date."""

from datetime import date, timedelta

from pipeline.strategies._time import parse_departure_time
from pipeline.timetable_in_force import Segment, Version, in_force, schedule_row, version_span

D = date(2026, 10, 14)


def _day(offset: int) -> date:
    return D + timedelta(days=offset)


def _on(segments: list[Segment], day: date) -> str | None:
    return next((s.stem for s in segments if s.first <= day <= s.last), None)


def test_a_version_whose_first_day_is_still_ahead_leaves_the_days_before_it_to_the_previous_one():
    segments = in_force(
        [
            Version("gtfs_static_20260914", _day(-30), _day(150)),
            Version("gtfs_static_20261014", _day(1), _day(180)),
        ]
    )
    assert segments == [
        Segment(_day(-30), D, "gtfs_static_20260914"),
        Segment(_day(1), _day(180), "gtfs_static_20261014"),
    ]
    assert _on(segments, D) == "gtfs_static_20260914"
    assert _on(segments, _day(1)) == "gtfs_static_20261014"


def test_overlapping_versions_give_each_day_to_the_newest_that_covers_it():
    segments = in_force(
        [
            Version("gtfs_static_20260101", date(2026, 1, 1), date(2026, 12, 31)),
            Version("gtfs_static_20260301", date(2026, 3, 1), date(2026, 6, 30)),
        ]
    )
    assert segments == [
        Segment(date(2026, 1, 1), date(2026, 2, 28), "gtfs_static_20260101"),
        Segment(date(2026, 3, 1), date(2026, 6, 30), "gtfs_static_20260301"),
        Segment(date(2026, 7, 1), date(2026, 12, 31), "gtfs_static_20260101"),
    ]


def test_newest_is_the_later_fetch_even_when_it_starts_earlier():
    segments = in_force(
        [
            Version("gtfs_static_20261014", date(2026, 10, 1), date(2027, 4, 1)),
            Version("gtfs_static_20261009", date(2026, 10, 8), date(2027, 4, 8)),
        ]
    )
    assert segments == [
        Segment(date(2026, 10, 1), date(2027, 4, 1), "gtfs_static_20261014"),
        Segment(date(2027, 4, 2), date(2027, 4, 8), "gtfs_static_20261009"),
    ]


def test_names_without_a_fetch_day_order_by_their_first_day_and_before_any_fetched_name():
    segments = in_force(
        [
            Version("legacy_b", date(2026, 2, 1), date(2026, 12, 31)),
            Version("legacy_a", date(2026, 1, 1), date(2026, 12, 31)),
            Version("gtfs_static_20250101", date(2026, 6, 1), date(2026, 6, 30)),
        ]
    )
    assert segments == [
        Segment(date(2026, 1, 1), date(2026, 1, 31), "legacy_a"),
        Segment(date(2026, 2, 1), date(2026, 5, 31), "legacy_b"),
        Segment(date(2026, 6, 1), date(2026, 6, 30), "gtfs_static_20250101"),
        Segment(date(2026, 7, 1), date(2026, 12, 31), "legacy_b"),
    ]


def test_a_date_no_version_covers_has_no_timetable_in_force():
    segments = in_force(
        [
            Version("gtfs_static_20260101", date(2026, 1, 1), date(2026, 3, 31)),
            Version("gtfs_static_20260401", date(2026, 4, 10), date(2026, 9, 30)),
        ]
    )
    assert _on(segments, date(2025, 12, 31)) is None
    assert _on(segments, date(2026, 4, 5)) is None
    assert _on(segments, date(2026, 10, 1)) is None
    assert _on(segments, date(2026, 4, 10)) == "gtfs_static_20260401"


def test_no_versions_put_nothing_in_force():
    assert in_force([]) == []


def test_feed_info_dates_are_the_span():
    ranges = [(date(2026, 1, 1), date(2026, 12, 31))]
    assert version_span("20260105", "20260630", ranges) == (date(2026, 1, 5), date(2026, 6, 30))


def test_without_feed_info_the_calendar_and_its_dates_give_the_span():
    ranges = [(date(2026, 1, 10), date(2026, 6, 30)), (date(2026, 1, 3), date(2026, 1, 3)), (date(2026, 7, 4),) * 2]
    assert version_span(None, None, ranges) == (date(2026, 1, 3), date(2026, 7, 4))


def test_feed_info_that_names_one_end_borrows_the_other_from_the_calendar():
    ranges = [(date(2026, 1, 10), date(2026, 6, 30))]
    assert version_span("20260201", "", ranges) == (date(2026, 2, 1), date(2026, 6, 30))
    assert version_span(None, "20260331", ranges) == (date(2026, 1, 10), date(2026, 3, 31))


def test_unreadable_or_inverted_feed_info_falls_back_to_the_calendar():
    ranges = [(date(2026, 1, 10), date(2026, 6, 30))]
    assert version_span("2026-02-01", "20260230", ranges) == (date(2026, 1, 10), date(2026, 6, 30))
    assert version_span("20260601", "20260101", ranges) == (date(2026, 1, 10), date(2026, 6, 30))


def test_a_timetable_with_no_dates_has_no_span():
    assert version_span(None, None, []) is None
    assert version_span(None, None, [(date(2026, 2, 1), date(2026, 1, 1))]) is None


def test_an_archived_schedule_row_carries_what_ingest_stamps():
    for raw in ("7:05:00", "07:05", "23:59:59", "25:30:00", "", None, "125:30:00", "7:5:00", " 08:00:00 "):
        sched, _status, sec = parse_departure_time(raw)
        assert schedule_row("t1", 3, "平日", "R1", raw) == ("t1", 3, "平日", "R1", sched, sec)
