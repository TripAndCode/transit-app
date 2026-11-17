"""The stop-by-stop comparison of two timetables (pipeline.reports.timetable_diff)."""

from __future__ import annotations

from datetime import date, timedelta

import pytest

from pipeline import static_archive
from pipeline.cache import DEGRADED_TTL_SECONDS
from pipeline.day_types import ServiceCalendar, WeeklyService
from pipeline.reports.timetable_diff import (
    Cell,
    Comparison,
    Revision,
    Run,
    TypicalDay,
    Unavailable,
    compare,
    counts_of,
    load_comparison,
    revisions,
    runs,
    service_calendar,
    stop_changes,
    typical_day,
    typical_days,
)
from pipeline.static_archive import ArchivedCalendar

WEEKDAYS = (True, True, True, True, True, False, False)
SATURDAY = (False, False, False, False, False, True, False)
SUNDAY = (False, False, False, False, False, False, True)
START, END = date(2026, 1, 1), date(2026, 12, 31)


def _calendar(*weekly: tuple[str, tuple[bool, ...]], exceptions=()) -> ServiceCalendar:
    return ServiceCalendar.of([WeeklyService(sid, days, START, END) for sid, days in weekly], exceptions)


def _hhmm(text: str) -> int:
    hh, mm = text.split(":")
    return int(hh) * 3600 + int(mm) * 60


def _cell(*times: str) -> Cell:
    secs = [_hhmm(t) for t in times]
    return Cell(len(secs), min(secs), max(secs))


def _stop(*times: str) -> dict[int, Cell]:
    by_hour: dict[int, list[str]] = {}
    for t in times:
        by_hour.setdefault(_hhmm(t) // 3600, []).append(t)
    return {hour: _cell(*ts) for hour, ts in by_hour.items()}


# ── runs and revisions ──────────────────────────────────────────────────────


def test_adjacent_versions_of_one_content_and_calendar_are_one_run_and_no_revision():
    segments = [
        (date(2026, 1, 1), date(2026, 3, 31), "v1", "c1"),
        (date(2026, 4, 1), date(2026, 4, 30), "v1b", "c1"),
        (date(2026, 5, 1), date(2026, 9, 30), "v2", "c2"),
    ]
    digests = {("v1", "c1"): "d1", ("v1b", "c1"): "d1", ("v2", "c2"): "d2"}
    found = runs(segments, digests)
    assert [(r.first, r.last, r.stem) for r in found] == [
        (date(2026, 1, 1), date(2026, 4, 30), "v1"),
        (date(2026, 5, 1), date(2026, 9, 30), "v2"),
    ]
    assert [rev.first_day for rev in revisions(found)] == [date(2026, 5, 1)]


def test_a_reissue_that_only_changes_the_calendar_is_a_revision():
    segments = [
        (date(2026, 1, 1), date(2026, 3, 31), "v1", "same"),
        (date(2026, 4, 1), date(2026, 9, 30), "v2", "same"),
    ]
    found = runs(segments, {("v1", "same"): "with-saturday", ("v2", "same"): "weekdays-only"})
    assert [rev.first_day for rev in revisions(found)] == [date(2026, 4, 1)]


def test_a_version_whose_calendar_is_not_kept_is_one_timetable_with_its_neighbour_of_one_content():
    segments = [
        (date(2026, 1, 1), date(2026, 3, 31), "v1", "same"),
        (date(2026, 4, 1), date(2026, 9, 30), "v1-reissued", "same"),
        (date(2026, 10, 1), date(2026, 12, 31), "v2", "other"),
    ]
    found = runs(segments, {("v1-reissued", "same"): "d1", ("v2", "other"): "d2"})
    assert [(r.first, r.last, r.stem, r.calendar) for r in found] == [
        (date(2026, 1, 1), date(2026, 9, 30), "v1-reissued", "d1"),
        (date(2026, 10, 1), date(2026, 12, 31), "v2", "d2"),
    ]
    assert [rev.first_day for rev in revisions(found)] == [date(2026, 10, 1)]


def test_a_run_whose_calendar_is_not_kept_carries_the_last_kept_one_across_gaps():
    segments = [
        (date(2026, 1, 1), date(2026, 3, 31), "v1", "c"),
        (date(2026, 4, 10), date(2026, 6, 30), "v2", "c"),
        (date(2026, 7, 10), date(2026, 9, 30), "v3", "c"),
    ]
    found = revisions(runs(segments, {("v1", "c"): "d1", ("v3", "c"): "d2"}))
    assert [(rev.first_day, rev.before.calendar, rev.before.stem, rev.after.calendar) for rev in found] == [
        (date(2026, 7, 10), "d1", "v1", "d2")
    ]
    assert found[0].before.first == date(2026, 4, 10)


def test_one_timetable_on_both_sides_of_a_gap_is_no_revision():
    segments = [
        (date(2026, 1, 1), date(2026, 3, 31), "v1", "c1"),
        (date(2026, 4, 10), date(2026, 9, 30), "v1", "c1"),
    ]
    assert revisions(runs(segments, {("v1", "c1"): "d1"})) == []


# ── typical days ────────────────────────────────────────────────────────────


def test_the_typical_day_is_the_service_set_most_dates_share_not_the_nearest_date():
    calendar = _calendar(
        ("weekday", WEEKDAYS),
        exceptions=[("weekday", date(2026, 3, 30), 2), ("holiday", date(2026, 3, 30), 1)],
    )
    dates = [date(2026, 3, 30), date(2026, 3, 27), date(2026, 3, 26)]
    assert typical_day(dates, calendar) == TypicalDay(date(2026, 3, 27), frozenset({"weekday"}))


def test_the_year_end_break_does_not_stand_for_weekdays():
    # 12-29 to 01-03 run the year-end timetable in place of the weekday one.
    exceptions = []
    day = date(2026, 12, 29)
    while day <= date(2027, 1, 3):
        exceptions += [("weekday", day, 2), ("yearend", day, 1)]
        day += timedelta(days=1)
    calendar = ServiceCalendar.of([WeeklyService("weekday", WEEKDAYS, date(2026, 4, 1), date(2027, 3, 31))], exceptions)
    for last in (date(2027, 1, 3), date(2027, 1, 4), date(2027, 1, 5)):
        run = Run(date(2026, 4, 1), last, "v", "c", "d")
        assert typical_days(run, calendar, latest=True)["weekday"].services == frozenset({"weekday"}), last


def test_a_calendar_dates_only_feed_reads_a_day_it_lists_nothing_for_as_known_and_empty():
    # Lists the dates each service runs and nothing else; Sunday service is dropped.
    days = [date(2026, 4, 1) + timedelta(days=i) for i in range(60)]
    weekday = [("w", d, 1) for d in days if d.weekday() < 5]
    calendar = ServiceCalendar.of([], weekday)
    run = Run(date(2026, 4, 1), date(2026, 12, 31), "v", "c", "d")
    found = typical_days(run, calendar, latest=False)
    assert found["sunday_holiday"] == TypicalDay(date(2026, 4, 5), frozenset())
    assert found["saturday"] == TypicalDay(date(2026, 4, 4), frozenset())
    # 2026-04-29 (昭和の日) runs weekday service but is one Sunday-type date of five.
    assert found["weekday"].services == frozenset({"w"})


def test_a_day_past_the_calendars_last_listed_day_is_not_sampled():
    calendar = ServiceCalendar.of([], [("w", date(2026, 4, 1), 1), ("w", date(2026, 4, 2), 1)])
    run = Run(date(2026, 3, 1), date(2026, 4, 30), "v", "c", "d")
    found = typical_days(run, calendar, latest=True)
    assert found["weekday"] == TypicalDay(date(2026, 4, 2), frozenset({"w"}))
    assert found["saturday"] is None


def test_a_tie_goes_to_the_set_met_first():
    calendar = _calendar(("a", WEEKDAYS), exceptions=[("a", date(2026, 3, 26), 2), ("b", date(2026, 3, 26), 1)])
    assert typical_day([date(2026, 3, 26), date(2026, 3, 27)], calendar).services == frozenset({"b"})


def test_dates_are_split_by_the_weekday_rule_with_public_holidays_as_sunday():
    # 2026-03-20 (Fri) is 春分の日: a public holiday, so it is a Sunday-type date.
    calendar = _calendar(("weekday", WEEKDAYS), ("saturday", SATURDAY), ("sunday", SUNDAY))
    run = Run(date(2026, 3, 1), date(2026, 3, 22), "v", "c", "d")
    days = typical_days(run, calendar, latest=True)
    assert days["weekday"].services == frozenset({"weekday"})
    assert days["saturday"] == TypicalDay(date(2026, 3, 21), frozenset({"saturday"}))
    assert days["sunday_holiday"].services == frozenset({"sunday"})
    assert days["sunday_holiday"].day == date(2026, 3, 22)


def test_a_weekday_public_holiday_is_a_sunday_type_date_not_a_weekday():
    # Monday to Friday, ending on 春分の日 (2026-03-20), which runs the Sunday timetable.
    calendar = _calendar(
        ("weekday", WEEKDAYS),
        ("sunday", SUNDAY),
        exceptions=[("weekday", date(2026, 3, 20), 2), ("sunday", date(2026, 3, 20), 1)],
    )
    run = Run(date(2026, 3, 16), date(2026, 3, 20), "v", "c", "d")
    days = typical_days(run, calendar, latest=True)
    assert days["sunday_holiday"] == TypicalDay(date(2026, 3, 20), frozenset({"sunday"}))
    assert days["weekday"] == TypicalDay(date(2026, 3, 19), frozenset({"weekday"}))


def test_the_side_after_a_revision_is_read_from_its_first_dates():
    calendar = _calendar(("weekday", WEEKDAYS), ("saturday", SATURDAY), ("sunday", SUNDAY))
    run = Run(date(2026, 4, 1), date(2026, 12, 31), "v", "c", "d")
    days = typical_days(run, calendar, latest=False)
    assert days["weekday"].day == date(2026, 4, 1)
    assert days["saturday"].day == date(2026, 4, 4)


def test_a_day_type_with_no_date_in_force_is_unknown_not_empty():
    calendar = _calendar(("weekday", WEEKDAYS), ("saturday", SATURDAY))
    run = Run(date(2026, 3, 23), date(2026, 3, 27), "v", "c", "d")  # Monday to Friday
    days = typical_days(run, calendar, latest=True)
    assert days["saturday"] is None
    assert days["weekday"] is not None


def test_a_covered_date_with_nothing_running_is_a_known_empty_day():
    calendar = _calendar(("weekday", WEEKDAYS))
    run = Run(date(2026, 3, 1), date(2026, 3, 31), "v", "c", "d")
    assert typical_days(run, calendar, latest=True)["saturday"].services == frozenset()


def test_dates_the_calendar_does_not_cover_are_not_sampled():
    calendar = ServiceCalendar.of([WeeklyService("w", WEEKDAYS, date(2026, 3, 2), date(2026, 3, 6))], [])
    run = Run(date(2026, 3, 1), date(2026, 3, 31), "v", "c", "d")
    days = typical_days(run, calendar, latest=True)
    assert days["weekday"].day == date(2026, 3, 6)
    assert days["saturday"] is None


def test_a_kept_calendar_reads_its_dates_and_skips_malformed_ones():
    archived = ArchivedCalendar(
        calendar=[("w", *WEEKDAYS, START, END)],
        calendar_dates=[("x", "20260321", 1), ("w", "2026032", 2), ("w", "20260323", 2)],
    )
    calendar = service_calendar(archived)
    assert calendar.running(date(2026, 3, 21)) == frozenset({"x"})
    assert calendar.running(date(2026, 3, 23)) == frozenset()
    assert calendar.running(date(2026, 3, 24)) == frozenset({"w"})


# ── stop changes ────────────────────────────────────────────────────────────


def test_lost_and_gained_count_each_hours_fall_and_rise_apart():
    before = {"weekday": {"S1": _stop("07:00", "07:30", "08:00", "21:00")}}
    after = {"weekday": {"S1": _stop("07:00", "08:00", "08:30", "21:00")}}
    [change] = stop_changes(before, after, ["weekday"])
    assert (change.before, change.after, change.lost, change.gained) == (4, 4, 1, 1)
    assert [(h.hour, h.before, h.after) for h in change.hours] == [(7, 2, 1), (8, 1, 2)]


def test_a_stop_losing_departures_counts_them_lost_not_gained():
    before = {"weekday": {"S1": _stop("07:00", "07:30", "08:00")}}
    after = {"weekday": {"S1": _stop("07:00")}}
    [change] = stop_changes(before, after, ["weekday"])
    assert (change.lost, change.gained) == (2, 0)


def test_the_last_bus_moved_earlier_is_a_negative_shift_even_within_one_hour():
    before = {"weekday": {"S1": _stop("06:30", "21:50")}}
    after = {"weekday": {"S1": _stop("06:30", "21:10")}}
    [change] = stop_changes(before, after, ["weekday"])
    assert change.hours == ()
    assert (change.last_before, change.last_after, change.last_shift_min) == (_hhmm("21:50"), _hhmm("21:10"), -40)


def test_a_later_first_departure_is_not_a_last_bus_shift():
    before = {"weekday": {"S1": _stop("06:00", "22:00")}}
    after = {"weekday": {"S1": _stop("06:30", "22:00")}}
    [change] = stop_changes(before, after, ["weekday"])
    assert change.last_shift_min == 0
    assert (change.first_before, change.first_after) == (_hhmm("06:00"), _hhmm("06:30"))


def test_a_stop_with_no_departures_left_has_ended_and_no_shift():
    before = {"saturday": {"S1": _stop("09:00", "17:00")}}
    after: dict = {"saturday": {}}
    [change] = stop_changes(before, after, ["saturday"])
    assert change.ended
    assert change.last_shift_min is None


def test_unchanged_stops_are_left_out():
    same = {"weekday": {"S1": _stop("07:00", "21:00")}}
    assert stop_changes(same, same, ["weekday"]) == []


def test_departures_past_midnight_keep_the_service_days_hours():
    before = {"weekday": {"S1": _stop("24:30")}}
    after: dict = {"weekday": {"S1": _stop("23:50")}}
    [change] = stop_changes(before, after, ["weekday"])
    assert [(h.hour, h.before, h.after) for h in change.hours] == [(23, 0, 1), (24, 1, 0)]
    assert change.last_shift_min == -40


# ── the comparison ──────────────────────────────────────────────────────────

_REVISION = Revision(
    Run(date(2026, 1, 1), date(2026, 3, 31), "v1", "c1", "d1"),
    Run(date(2026, 4, 1), date(2026, 9, 30), "v2", "c2", "d2"),
)


def _day(d: date, *services: str) -> TypicalDay:
    return TypicalDay(d, frozenset(services))


def test_each_day_type_is_compared_on_its_own_typical_day():
    before_days = {
        "weekday": _day(date(2026, 3, 31), "w"),
        "saturday": _day(date(2026, 3, 28), "sa"),
        "sunday_holiday": _day(date(2026, 3, 29), "su"),
    }
    after_days = {
        "weekday": _day(date(2026, 4, 1), "w"),
        "saturday": _day(date(2026, 4, 4)),
        "sunday_holiday": _day(date(2026, 4, 5), "su"),
    }
    before = {
        "weekday": {"S1": _stop("07:00", "22:00")},
        "saturday": {"S1": _stop("09:00")},
        "sunday_holiday": {"S1": _stop("10:00")},
    }
    after = {
        "weekday": {"S1": _stop("07:00", "21:30")},
        "saturday": {},
        "sunday_holiday": {"S1": _stop("10:00")},
    }
    result = compare(_REVISION, before_days, after_days, before, after)
    by_type = {s.day_type: s for s in result.day_types}
    assert (by_type["weekday"].departures_before, by_type["weekday"].departures_after) == (2, 2)
    assert by_type["weekday"].last_earlier == 1
    assert (by_type["saturday"].departures_before, by_type["saturday"].departures_after) == (1, 0)
    assert by_type["saturday"].stops_ended == 1
    assert by_type["sunday_holiday"].stops_ended == 0
    assert [(s.day_type, s.ended) for s in result.stops] == [("saturday", True), ("weekday", False)]


def test_a_day_type_unknown_on_either_side_reports_no_changes_and_no_stop_counts():
    before_days = {"weekday": _day(date(2026, 3, 31), "w"), "saturday": None, "sunday_holiday": None}
    after_days = {
        "weekday": _day(date(2026, 4, 1), "w"),
        "saturday": _day(date(2026, 4, 4)),
        "sunday_holiday": None,
    }
    before = {"weekday": {"S1": _stop("07:00")}, "saturday": {"S1": _stop("09:00")}}
    after = {"weekday": {"S1": _stop("07:00")}, "saturday": {}}
    result = compare(_REVISION, before_days, after_days, before, after)
    saturday = next(s for s in result.day_types if s.day_type == "saturday")
    assert saturday.before is None
    assert (saturday.departures_before, saturday.departures_after, saturday.stops_ended) == (None, 0, None)
    assert result.stops == ()


def test_counts_are_read_by_day_type_in_their_order():
    counts = counts_of([(1, "S1", [(7, 2, 25200, 27000)]), (2, "S1", [(9, 1, 32400, 32400)])])
    assert counts["weekday"]["S1"][7] == Cell(2, 25200, 27000)
    assert counts["saturday"]["S1"][9] == Cell(1, 32400, 32400)
    assert counts["sunday_holiday"] == {}


# ── loading, with the comparison cached ─────────────────────────────────────


class _Result:
    def __init__(self, rows):
        self.result_rows = rows


class _FakeArchive:
    """An async ClickHouse stand-in holding one stop's weekday departures per
    content, and a calendar per (version, content) by its digest: "weekdays"
    runs service w Monday to Friday, "none" runs nothing."""

    def __init__(self):
        self.departure_reads = 0

    async def query(self, sql, parameters=None):
        if "static_calendar_archive" in sql:
            flags = WEEKDAYS if parameters["stem"].endswith("weekdays") else (False,) * 7
            return _Result([("calendar", "w", *flags, START.isoformat(), END.isoformat(), None, None)])
        self.departure_reads += 1
        hours = [(7, 2, 25200, 27000)] if parameters["content"] == "c1" else [(7, 1, 25200, 25200)]
        return _Result([(1, "S1", hours)] if parameters["services"][0] else [])


def _revision_with(after_calendar: str) -> Revision:
    return Revision(
        Run(date(2026, 1, 1), date(2026, 3, 31), "v1-weekdays", "c1", "weekdays"),
        Run(date(2026, 4, 1), date(2026, 9, 30), f"v2-{after_calendar}", "c2", after_calendar),
    )


async def test_a_comparison_is_cached_by_its_revision_calendars_included():
    archive = _FakeArchive()
    weekdays = await load_comparison(archive, 1, _revision_with("weekdays"))
    again = await load_comparison(archive, 1, _revision_with("weekdays"))
    assert again is weekdays
    assert archive.departure_reads == 2
    nothing = await load_comparison(archive, 1, _revision_with("none"))
    # Read again for the before side; an after side running nothing reads no departures.
    assert archive.departure_reads == 3
    by_type = {s.day_type: s for s in nothing.day_types}
    assert by_type["weekday"].departures_after == 0
    assert {s.day_type: s for s in weekdays.day_types}["weekday"].departures_after == 1


async def test_a_calendar_its_digest_names_but_the_archive_lacks_is_unavailable_only_briefly(cache_clock, monkeypatch):
    archive, revision = _FakeArchive(), _revision_with("weekdays")
    real, reads = static_archive.archived_calendar_async, []

    async def missing_at_first(*args):
        reads.append(args)
        return None if len(reads) <= 2 else await real(*args)

    monkeypatch.setattr(static_archive, "archived_calendar_async", missing_at_first)
    assert await load_comparison(archive, 1, revision) is Unavailable.NO_CALENDAR
    cache_clock.now += DEGRADED_TTL_SECONDS + 1
    assert isinstance(await load_comparison(archive, 1, revision), Comparison)


@pytest.mark.parametrize("missing", ["before", "after"])
async def test_a_side_with_no_kept_calendar_is_unavailable(missing):
    revision = _revision_with("weekdays")
    run = getattr(revision, missing)._replace(calendar=None)
    result = await load_comparison(_FakeArchive(), 1, revision._replace(**{missing: run}))
    assert result.value == "no_calendar"
