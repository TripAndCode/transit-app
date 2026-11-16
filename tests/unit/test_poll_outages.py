"""Poll success and outage minutes, from poll instants and the service hours
of the days they count, without a database."""

from datetime import date, datetime, timedelta, timezone

import pytest

from pipeline import poll_outages
from pipeline.poll_outages import (
    JST,
    OUTAGE_FLOOR_SEC,
    CountedSpans,
    Outage,
    columns,
    counted_spans,
    live_held,
    merged_polls,
    peer_batches,
    poll_figures,
    split,
    threshold_sec,
)
from pipeline.service_day import service_day_start_utc

_DAY = date(2026, 10, 5)
_DAY_START = int(service_day_start_utc(_DAY).timestamp())
# 06:00 to 22:00 JST: two hours and eighteen hours after the 04:00 cutover.
_HOURS = (6 * 3600, 22 * 3600)
_OPEN = _DAY_START + 2 * 3600
_CLOSE = _DAY_START + 18 * 3600
_WINDOW = (_DAY_START, _DAY_START + 86400)


def _every(start: int, end: int, step: int = 30) -> list[int]:
    return list(range(start, end + 1, step))


def test_the_interval_is_the_median_gap_between_polls_inside_service():
    polls = [*_every(_OPEN, _OPEN + 600), _OPEN + 660, _OPEN + 700]
    figures = poll_figures(polls, [(_OPEN, _CLOSE)], _WINDOW)
    assert figures.interval_sec == 30.0


def test_a_gap_outside_service_is_not_an_outage():
    # The feed is silent from 04:00 until 30 s after service starts at 06:00.
    polls = [_OPEN - 7200, *_every(_OPEN + 30, _CLOSE)]
    figures = poll_figures(polls, [(_OPEN, _CLOSE)], _WINDOW)
    assert figures.outages == []
    assert figures.in_service_sec == _CLOSE - _OPEN


def test_only_the_part_of_a_gap_inside_service_counts():
    # The last poll is at 21:00; service runs to 22:00.
    polls = _every(_OPEN, _CLOSE - 3600)
    figures = poll_figures(polls, [(_OPEN, _CLOSE)], _WINDOW)
    [outage] = figures.outages
    assert (outage.start, outage.end) == (_CLOSE - 3600 + 30, _CLOSE)
    # The raw gap runs to the window's end, where no later poll is.
    assert (outage.gap_start, outage.gap_end) == (_CLOSE - 3600, _WINDOW[1])


def test_a_gap_from_service_start_to_the_first_poll_is_an_outage_from_the_start_of_service():
    polls = [_OPEN - 3600, *_every(_OPEN + 1800, _CLOSE)]
    [outage] = poll_figures(polls, [(_OPEN, _CLOSE)], _WINDOW).outages
    # The next poll was due long before service began, so all of it counts.
    assert (outage.start, outage.end) == (_OPEN, _OPEN + 1800)
    assert (outage.gap_start, outage.gap_end) == (_OPEN - 3600, _OPEN + 1800)


def test_a_gap_opened_just_before_service_counts_from_when_the_next_poll_was_due():
    polls = [_OPEN - 10, *_every(_OPEN + 1800, _CLOSE)]
    [outage] = poll_figures(polls, [(_OPEN, _CLOSE)], _WINDOW).outages
    assert (outage.start, outage.end) == (_OPEN + 20, _OPEN + 1800)


def test_a_day_without_a_poll_is_one_outage_over_its_service():
    figures = poll_figures(
        [*_every(_OPEN - 86400, _CLOSE - 86400)], [(_OPEN - 86400, _CLOSE - 86400), (_OPEN, _CLOSE)], _WINDOW
    )
    assert figures.interval_sec == 30.0
    assert [(o.start, o.end) for o in figures.outages] == [(_OPEN, _CLOSE)]


def test_a_gap_counts_only_beyond_the_threshold_and_only_its_time_beyond_one_interval():
    assert threshold_sec(30.0) == OUTAGE_FLOOR_SEC
    assert threshold_sec(200.0) == 600.0
    short = [*_every(_OPEN, _OPEN + 3000), *_every(_OPEN + 3000 + OUTAGE_FLOOR_SEC, _CLOSE)]
    assert poll_figures(short, [(_OPEN, _CLOSE)], _WINDOW).outages == []
    long = [*_every(_OPEN, _OPEN + 3000), *_every(_OPEN + 3000 + OUTAGE_FLOOR_SEC + 30, _CLOSE)]
    [outage] = poll_figures(long, [(_OPEN, _CLOSE)], _WINDOW).outages
    assert outage.end - outage.start == OUTAGE_FLOOR_SEC


def test_fewer_than_two_polls_inside_service_leave_the_interval_unknown():
    figures = poll_figures([_OPEN + 60], [(_OPEN, _CLOSE)], _WINDOW)
    assert figures.interval_sec is None
    assert figures.outages == []
    assert figures.polls == 1


def test_polls_are_counted_inside_service_only():
    polls = [_OPEN - 30, *_every(_OPEN, _CLOSE), _CLOSE + 30]
    assert poll_figures(polls, [(_OPEN, _CLOSE)], _WINDOW).polls == len(_every(_OPEN, _CLOSE))


def test_a_stretch_no_other_agency_was_polled_in_is_the_collectors_not_the_feeds():
    outage = Outage(gap_start=_OPEN, gap_end=_OPEN + 3600, start=_OPEN + 30, end=_OPEN + 3600)
    # Every other agency stopped from 00:20 to 00:50 into the gap.
    collector = (_OPEN + 1200, _OPEN + 3000)
    feed_sec, feed_longest, collector_sec, collector_longest = split([outage], {0: [collector]})
    assert collector_sec == collector_longest == 1800
    assert feed_sec == 3570 - 1800
    # The feed's time is in two parts either side of the collector's: the longest is one gap's.
    assert feed_longest == 3570 - 1800


def test_without_a_shared_stretch_every_outage_minute_is_the_feeds():
    outages = [
        Outage(_OPEN, _OPEN + 900, _OPEN + 30, _OPEN + 900),
        Outage(_OPEN + 5000, _OPEN + 7000, _OPEN + 5030, _OPEN + 7000),
    ]
    assert split(outages, {}) == (870 + 1970, 1970, 0, 0)


def test_a_shared_stretch_outside_the_outage_counts_nothing():
    outage = Outage(_OPEN, _OPEN + 900, _OPEN + 30, _OPEN + 900)
    # The collector stopped before service began; the feed resumed late on its own.
    assert split([outage], {0: [(_OPEN - 3600, _OPEN + 10)]}) == (870, 870, 0, 0)


def _hours(*days: date) -> dict[date, tuple[int, int] | None]:
    return dict.fromkeys(days, _HOURS)


def test_spans_count_the_days_whose_hours_are_known_from_the_first_observed_day_through_the_coverage_day():
    days = [_DAY + timedelta(days=i) for i in range(5)]
    hours = {**_hours(*days), days[2]: None}
    del hours[days[3]]
    counted = counted_spans(hours, first=days[0], through=days[3], observed_from=days[1], live_held=set())
    # Day 0 is before the agency was first observed, day 2 runs nothing, day 3's
    # hours are unknown and day 4 is past the coverage day.
    assert (counted.known, counted.days, counted.serviced) == (2, 1, True)
    start = int(service_day_start_utc(days[1]).timestamp())
    assert counted.spans == [(start + 2 * 3600, start + 18 * 3600)]


def test_a_calendar_day_the_live_path_holds_is_cut_out_of_service():
    days = [_DAY, _DAY + timedelta(days=1)]
    counted = counted_spans(_hours(*days), first=days[0], through=days[1], observed_from=None, live_held={days[1]})
    assert (len(counted.spans), counted.days, counted.known) == (1, 1, 2)
    gone = counted_spans(_hours(_DAY), first=_DAY, through=_DAY, observed_from=None, live_held={_DAY})
    assert (gone.spans, gone.serviced) == ([], True)


def test_hours_past_the_next_cutover_stay_on_their_own_service_day():
    hours = {_DAY: (5 * 3600, 29 * 3600)}
    [span] = counted_spans(hours, first=_DAY, through=_DAY, observed_from=None, live_held=set()).spans
    assert span == (_DAY_START + 3600, _DAY_START + 86400)


def _jst(day: int, hour: int, minute: int = 0) -> int:
    return int(datetime(2026, 10, day, hour, minute, tzinfo=JST).timestamp())


def _late_service(archive: list[int], live: list[int], days: int = 1):
    """*days* days' service from 5 October, 05:00 to 24:40 JST, over the merged polls."""
    first, last = _DAY, _DAY + timedelta(days=days - 1)
    hours = {first + timedelta(days=i): (5 * 3600, 24 * 3600 + 40 * 60) for i in range(days)}
    counted = counted_spans(hours, first=first, through=last, observed_from=None, live_held=live_held(live))
    return poll_figures(merged_polls(archive, live), counted.spans, (_jst(5, 4), _jst(5 + days + 1, 4)))


def test_the_live_path_s_pushes_after_midnight_count_when_the_archive_holds_the_day_before():
    # 5 October is held from the archive, 6 October from the live path, which
    # carries the service day on past midnight.
    archive = list(range(_jst(5, 4), _jst(6, 0), 30))
    live = list(range(_jst(6, 0), _jst(7, 0), 300))
    assert live_held(live) == {date(2026, 10, 6)}
    figures = _late_service(archive, live)
    assert figures.interval_sec == 30.0
    # The hours after midnight are the live path's sample, not the feed's gaps.
    assert figures.outages == []


def test_a_live_day_before_an_archived_night_leaves_only_the_archived_hours():
    live = list(range(_jst(5, 4), _jst(6, 0), 300))
    archive = list(range(_jst(6, 0), _jst(6, 4), 15))
    assert live_held(live) == {date(2026, 10, 5)}
    figures = _late_service(archive, live)
    assert (figures.interval_sec, figures.outages) == (15.0, [])
    assert figures.in_service_sec == 40 * 60


def _columns(**over):
    full = poll_figures(_every(_OPEN, _CLOSE), [(_OPEN, _CLOSE)], _WINDOW)
    args = {
        "polls_seen": 100,
        "undated_polls": 0,
        "counted": CountedSpans([(_OPEN, _CLOSE)], 1, 1, True),
        "figures": full,
        "split": (0, 0, 0, 0),
        "peer_rows": 10,
    }
    return columns(**{**args, **over})


def test_the_columns_are_the_ones_the_scorecard_stores():
    assert set(_columns()) == set(poll_outages.COLUMNS)


def test_success_is_the_polls_recorded_of_those_expected_over_service():
    # One poll every 30 s from 06:00 through 22:00: 1,921 recorded, 1,920 expected.
    row = _columns()
    assert (row["polls_in_service"], row["polls_expected"]) == (1921, 1920)
    assert row["poll_success_pct"] == 100.0
    half = poll_figures(_every(_OPEN, _CLOSE - 60, 60), [(_OPEN, _CLOSE)], _WINDOW)
    assert _columns(figures=half._replace(interval_sec=30.0))["poll_success_pct"] == 50.0


def test_success_never_exceeds_100():
    dense = poll_figures(_every(_OPEN, _CLOSE), [(_OPEN, _CLOSE)], _WINDOW)
    assert _columns(figures=dense._replace(interval_sec=60.0))["poll_success_pct"] == 100.0


def test_outage_minutes_are_split_into_the_feeds_and_the_collectors():
    row = _columns(split=(1770, 1200, 1800, 1800))
    assert (row["feed_outage_min"], row["feed_outage_longest_min"]) == (29.5, 20.0)
    assert (row["collector_outage_min"], row["collector_outage_longest_min"]) == (30.0, 30.0)
    assert row["outage_missing"] is None


@pytest.mark.parametrize(
    ("over", "reason"),
    [
        ({"polls_seen": 0}, "no_data"),
        ({"undated_polls": 1}, "undated_polls"),
        ({"counted": CountedSpans([], 0, 0, False)}, "no_service_hours"),
        ({"counted": CountedSpans([], 0, 1, False)}, "no_plan"),
        ({"counted": CountedSpans([], 0, 1, True)}, "live_sampled"),
    ],
)
def test_a_month_without_poll_figures_says_why_and_never_reads_zero(over, reason):
    spans = over["counted"].spans if "counted" in over else [(_OPEN, _CLOSE)]
    empty = poll_figures([], spans, _WINDOW)
    row = _columns(figures=empty, **over)
    assert row["polls_missing"] == row["outage_missing"] == reason
    for column in poll_outages.FIGURES:
        assert row[column] is None, column


def test_an_unknown_interval_leaves_every_figure_unknown():
    lone = poll_figures([_OPEN + 60], [(_OPEN, _CLOSE)], _WINDOW)
    row = _columns(figures=lone)
    assert row["polls_missing"] == "no_poll_interval"
    assert row["poll_success_pct"] is None and row["feed_outage_min"] is None


def test_an_outage_with_no_other_agency_polled_has_no_split():
    gappy = poll_figures([*_every(_OPEN, _OPEN + 3000), *_every(_OPEN + 6000, _CLOSE)], [(_OPEN, _CLOSE)], _WINDOW)
    row = _columns(figures=gappy, peer_rows=0)
    assert row["poll_success_pct"] is not None
    assert row["outage_missing"] == "no_peer"
    assert row["feed_outage_min"] is None and row["collector_outage_min"] is None


def test_a_month_without_an_outage_needs_no_other_agency_to_say_so():
    row = _columns(peer_rows=0)
    assert (row["feed_outage_min"], row["collector_outage_min"], row["outage_missing"]) == (0.0, 0.0, None)


def test_a_calendar_day_holding_any_live_poll_is_live_held_whatever_archived_polls_it_holds():
    # A day whose replacement has not settled holds both: held as live.
    archive = [_OPEN, _OPEN + 30]
    pushed = [_OPEN + 3, _OPEN + 2 * 86400]
    assert live_held(pushed) == {_DAY, _DAY + timedelta(days=2)}
    assert merged_polls(archive, pushed) == [_OPEN, _OPEN + 3, _OPEN + 30, _OPEN + 2 * 86400]


@pytest.mark.parametrize(
    "straddler",
    [
        # Header-stamped: its header, just before midnight, stored it on the 5th.
        _jst(6, 0) + 10,
        # Headerless, named 16:00 UTC: archive ingest read the name as JST, the 5th.
        int(datetime(2026, 10, 5, 16, 0, tzinfo=timezone.utc).timestamp()),
    ],
)
def test_an_archived_poll_stored_on_the_day_before_its_instant_leaves_the_live_day_live(straddler):
    # 5 October was replaced from the archive; 6 October keeps the live path's
    # pushes. One archived row of the 5th names an instant on the 6th.
    archive = [*range(_jst(5, 4), _jst(6, 0), 30), straddler]
    live = list(range(_jst(6, 0), _jst(7, 4), 300))
    assert live_held(live) == {date(2026, 10, 6), date(2026, 10, 7)}
    figures = _late_service(archive, live, days=2)
    assert figures.interval_sec == 30.0
    assert figures.outages == []


def test_outages_whose_scans_overlap_share_one_and_the_rest_scan_only_around_their_own():
    first = Outage(_OPEN, _OPEN + 900, _OPEN + 30, _OPEN + 900)
    second = Outage(_OPEN + 4000, _OPEN + 5000, _OPEN + 4030, _OPEN + 5000)
    later = Outage(_OPEN + 20 * 86400, _OPEN + 20 * 86400 + 600, _OPEN + 20 * 86400 + 30, _OPEN + 20 * 86400 + 600)
    batches = peer_batches([later, first, second], reach=300, stored_reach=3600)
    assert [(b.scan_from, b.scan_to, b.outages) for b in batches] == [
        (_OPEN - 3900, _OPEN + 8900, [1, 2]),
        (later.gap_start - 3900, later.gap_end + 3900, [0]),
    ]
    assert (batches[0].los, batches[0].his) == ([_OPEN - 300, _OPEN + 3700], [_OPEN + 1200, _OPEN + 5300])


def test_a_kept_timetable_without_trips_to_read_leaves_its_days_unknown(monkeypatch):
    from pipeline import static_archive
    from pipeline.static_archive import ArchivedCalendar, ScheduleJoin
    from pipeline.timetable_in_force import Segment

    join = ScheduleJoin(["v1"], ["c1"], "", (Segment(_DAY, _DAY, "v1"),))
    calendar = ArchivedCalendar([("ALL", *([True] * 7), _DAY, _DAY)], [])
    monkeypatch.setattr(static_archive, "schedule_join", lambda *a, **k: join)
    monkeypatch.setattr(static_archive, "archived_calendar", lambda *a: calendar)
    monkeypatch.setattr(poll_outages, "hours_of", lambda *a, **k: pytest.fail("no trips, no hours to read"))

    class _NoTrips:
        def query(self, sql, parameters):
            return type("Result", (), {"result_rows": []})()

    assert poll_outages.in_force_hours(None, _NoTrips(), 8, _DAY, _DAY) == {}
