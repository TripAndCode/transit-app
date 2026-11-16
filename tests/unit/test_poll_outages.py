"""Poll success and outage minutes, from poll instants and the service hours
of the days they count, without a database."""

from datetime import date, timedelta

import pytest

from pipeline import poll_outages
from pipeline.poll_outages import (
    OUTAGE_FLOOR_SEC,
    Outage,
    columns,
    counted_spans,
    live_days,
    merged_polls,
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


def test_a_gap_from_service_start_to_the_first_poll_is_an_outage():
    polls = [_OPEN - 3600, *_every(_OPEN + 1800, _CLOSE)]
    [outage] = poll_figures(polls, [(_OPEN, _CLOSE)], _WINDOW).outages
    assert (outage.start, outage.end) == (_OPEN + 30, _OPEN + 1800)
    assert (outage.gap_start, outage.gap_end) == (_OPEN - 3600, _OPEN + 1800)


def test_a_day_without_a_poll_is_one_outage_over_its_service():
    figures = poll_figures(
        [*_every(_OPEN - 86400, _CLOSE - 86400)], [(_OPEN - 86400, _CLOSE - 86400), (_OPEN, _CLOSE)], _WINDOW
    )
    assert figures.interval_sec == 30.0
    assert [(o.start, o.end) for o in figures.outages] == [(_OPEN + 30, _CLOSE)]


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
    spans, known = counted_spans(hours, first=days[0], through=days[3], observed_from=days[1], live_days=set())
    # Day 0 is before the agency was first observed, day 2 runs nothing, day 3's
    # hours are unknown and day 4 is past the coverage day.
    assert known == 2
    start = int(service_day_start_utc(days[1]).timestamp())
    assert spans == [(start + 2 * 3600, start + 18 * 3600)]


def test_a_day_the_live_path_holds_is_left_out():
    days = [_DAY, _DAY + timedelta(days=1)]
    spans, known = counted_spans(_hours(*days), first=days[0], through=days[1], observed_from=None, live_days={days[1]})
    assert (len(spans), known) == (1, 2)


def test_hours_past_the_next_cutover_stay_on_their_own_service_day():
    hours = {_DAY: (5 * 3600, 29 * 3600)}
    [span] = counted_spans(hours, first=_DAY, through=_DAY, observed_from=None, live_days=set())[0]
    assert span == (_DAY_START + 3600, _DAY_START + 86400)


def _columns(**over):
    full = poll_figures(_every(_OPEN, _CLOSE), [(_OPEN, _CLOSE)], _WINDOW)
    args = {
        "total_rows": 100,
        "undated_polls": 0,
        "known_days": 1,
        "spans": [(_OPEN, _CLOSE)],
        "all_live": False,
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
        ({"total_rows": 0}, "no_data"),
        ({"undated_polls": 1}, "undated_polls"),
        ({"known_days": 0, "spans": []}, "no_service_hours"),
        ({"spans": []}, "no_plan"),
        ({"spans": [], "all_live": True}, "live_sampled"),
    ],
)
def test_a_month_without_poll_figures_says_why_and_never_reads_zero(over, reason):
    empty = poll_figures([], over.get("spans", [(_OPEN, _CLOSE)]), _WINDOW)
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


def test_a_day_most_of_whose_polls_came_through_the_live_path_is_a_live_day():
    archive = _every(_OPEN, _OPEN + 300)
    pushed = [_OPEN + 86400, _OPEN + 86400 + 300, _OPEN + 3]
    assert live_days(archive, pushed) == {_DAY + timedelta(days=1)}


def test_a_day_held_from_the_archive_keeps_only_its_archived_polls():
    archive = [_OPEN, _OPEN + 30]
    # A pushed copy of the first poll, stamped on arrival; and the next day's pushes.
    pushed = [_OPEN + 2, _OPEN + 86400, _OPEN + 86400 + 300]
    days = live_days(archive, pushed)
    assert merged_polls(archive, pushed, days) == [_OPEN, _OPEN + 30, _OPEN + 86400, _OPEN + 86400 + 300]
