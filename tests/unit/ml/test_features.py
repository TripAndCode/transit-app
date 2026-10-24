from datetime import date, timedelta

from tests.unit.ml.ml_group import require

pd = require("pandas")
np = require("numpy")

from ml.features import (  # noqa: E402
    FEATURES,
    build_frame,
    route_ids,
    template_future_runs,
    template_span,
    weekend_days,
    with_route_ids,
)
from pipeline.day_types import WeeklyService, fallback_day_type, resolve_day_types  # noqa: E402

ORIGIN = date(2026, 9, 8)  # a Tuesday


def _day(offset: int) -> date:
    return ORIGIN + timedelta(days=offset)


def _runs(rows):
    """rows: (day offset, route, trip, hour, delay, service)."""
    frame = pd.DataFrame(
        [
            {
                "agency_id": 8,
                "route_code": route,
                "trip_id": trip,
                "service_date": pd.Timestamp(_day(offset)),
                "hour": hour,
                "service": service,
                "delay_min": delay,
                "stops": 10,
                "span_min": 30.0,
            }
            for offset, route, trip, hour, delay, service in rows
        ]
    ).astype(
        {
            "agency_id": "int16",
            "route_code": "string",
            "trip_id": "string",
            "hour": "int16",
            "service": "string",
            "delay_min": "float32",
            "stops": "int16",
            "span_min": "float32",
        }
    )
    return with_route_ids(frame, route_ids(frame))


def test_a_run_gets_b0_and_b1_from_its_slot_and_route_trends_from_recent_days():
    runs = _runs(
        [
            (-14, "R1", "T1", 8, 4.0, "wk"),
            (-7, "R1", "T1", 8, 2.0, "wk"),
            (-1, "R1", "T2", 8, 9.0, "wk"),
            (7, "R1", "T1", 8, 0.0, "wk"),
        ]
    )
    row = build_frame(runs, ORIGIN).iloc[0]
    assert row.h == 7
    assert row.slot_mean == 3.0 and row.slot_runs == 2 and row.slot_last == 2.0
    assert row.route_mean == 5.0 and row.route_prev == 9.0
    assert row.route_mean_7 == 5.5 and row.route_mean_3 == 9.0 and row.route_trend == 4.0
    assert row.trip_mean == 3.0 and row.trip_runs == 2
    assert row.agency_days == 3


def test_no_feature_reads_the_origin_day_or_later():
    history = [(-d, "R1", f"T{d % 3}", 8 + d % 2, float(d % 5), "wk") for d in range(1, 40)]
    future = [(d, "R1", f"T{d % 3}", 8 + d % 2, 1.0, "wk") for d in range(0, 9)]
    runs = _runs(history + future)
    later = runs["service_date"] >= pd.Timestamp(ORIGIN)
    shuffled = runs.assign(delay_min=runs["delay_min"].where(~later, 99.0))
    before, after = build_frame(runs, ORIGIN), build_frame(shuffled, ORIGIN)
    pd.testing.assert_frame_equal(before[FEATURES], after[FEATURES])


def test_a_route_with_no_history_still_gets_a_row_with_its_history_missing():
    runs = _runs([(-7, "R1", "T1", 8, 2.0, "wk"), (1, "NEW", "N1", 8, 3.0, "wk")])
    row = build_frame(runs, ORIGIN).set_index("route_code").loc["NEW"]
    assert np.isnan(row.slot_mean) and row.slot_runs == 0 and np.isnan(row.route_mean)


def test_an_origin_with_no_history_at_all_still_builds_its_rows():
    runs = _runs([(1, "R1", "T1", 8, 2.0, "wk")])
    frame = build_frame(runs, ORIGIN)
    assert len(frame) == 1 and frame.iloc[0].agency_days == 0


def test_a_weekend_service_on_a_weekday_reads_as_a_holiday():
    saturdays = [(-3 - 7 * k, "R1", "S", 9, 1.0, "holiday-svc") for k in range(4)]  # -3 is a Saturday
    weekdays = [(-1 - 7 * k, "R1", "W", 8, 1.0, "weekday-svc") for k in range(4)]
    targets = [(6, "R1", "S", 9, 0.0, "holiday-svc"), (6, "R1", "W", 8, 0.0, "weekday-svc")]  # +6 is a Monday
    frame = build_frame(_runs(saturdays + weekdays + targets), ORIGIN).set_index("trip_id")
    assert frame.loc["S"].service_weekend_share == 1.0
    assert frame.loc["W"].service_weekend_share == 0.0


def test_a_gap_day_is_missing_from_every_window_not_zero():
    runs = _runs([(-d, "R1", "T1", 8, 4.0, "wk") for d in range(2, 30)] + [(1, "R1", "T1", 8, 0.0, "wk")])
    row = build_frame(runs, ORIGIN).iloc[0]
    assert row.route_mean == 4.0 and np.isnan(row.route_prev)


def test_a_precomputed_weekend_table_on_the_same_runs_gives_the_same_frame():
    runs = _runs(
        [
            (-3, "R1", "S", 9, 1.0, "holiday-svc"),
            (-10, "R1", "S", 9, 1.0, "holiday-svc"),
            (-1, "R1", "W", 8, 1.0, "weekday-svc"),
            (6, "R1", "S", 9, 0.0, "holiday-svc"),
            (6, "R1", "W", 8, 0.0, "weekday-svc"),
        ]
    )
    direct = build_frame(runs, ORIGIN)
    precomputed = build_frame(runs, ORIGIN, precomputed_weekend_days=weekend_days(runs))
    pd.testing.assert_frame_equal(direct[FEATURES], precomputed[FEATURES])


def test_weekend_share_does_not_see_rows_on_or_after_the_origin():
    history = [(-3 - 7 * k, "R1", "S", 9, 1.0, "holiday-svc") for k in range(2)]  # weekend-only so far
    leaked_future = [(d, "R1", "LEAK", 9, 1.0, "holiday-svc") for d in range(0, 5)]  # same service, on/after origin
    target = [(6, "R1", "S", 9, 0.0, "holiday-svc")]
    runs = _runs(history + leaked_future + target)
    table = weekend_days(runs)  # built over rows that include origin day and later
    frame = build_frame(runs, ORIGIN, precomputed_weekend_days=table)
    assert frame.set_index("trip_id").loc["S"].service_weekend_share == 1.0


_TIMETABLE = {"weekday": "W", "saturday": "S", "sunday_holiday": "H"}


def _timetable(
    first: date, last: date, *, agency_id: int = 8, slots: int = 4, short=None, only=None, hours=None, kinds=None
):
    """An agency running, on every date of [first, last], the timetable the
    weekday rule gives that date (or ``kinds[d]``): `slots` hourly runs from
    06:00 whose trip ids name the timetable and the date (``W1016-6`` is 10/16's
    06:00 weekday run), or ``short[d]`` runs on a day cut off early. ``hours``
    replaces the hourly runs with runs at those hours alone; ``only`` keeps the
    dates of those timetables alone."""
    short = short or {}
    kinds = kinds or {}
    rows = []
    d = first
    while d <= last:
        kind = kinds.get(d) or fallback_day_type(d)
        if only is None or kind in only:
            for hour in hours or range(6, 6 + short.get(d, slots)):
                rows.append(
                    {
                        "agency_id": agency_id,
                        "route_code": "R1",
                        "trip_id": f"{_TIMETABLE[kind]}{d:%m%d}-{hour}",
                        "service_date": pd.Timestamp(d),
                        "hour": hour,
                        "service": kind,
                        "delay_min": 1.0,
                        "stops": 10,
                        "span_min": 30.0,
                    }
                )
        d += timedelta(days=1)
    return pd.DataFrame(rows).astype(
        {
            "agency_id": "int16",
            "route_code": "string",
            "trip_id": "string",
            "hour": "int16",
            "service": "string",
            "delay_min": "float32",
            "stops": "int16",
            "span_min": "float32",
        }
    )


def _sources(template, d: date) -> set[str]:
    """The days the template copied onto `d`, as their trip-id prefixes."""
    rows = template[template["service_date"] == pd.Timestamp(d)]
    return {trip.split("-")[0] for trip in rows["trip_id"]}


def test_template_future_runs_copies_the_most_recent_same_type_day_forward_with_its_delay_unknown():
    runs = _timetable(date(2026, 10, 5), date(2026, 10, 16))
    template = template_future_runs(runs, date(2026, 10, 17), horizon_days=1)
    # Sunday from the last Sunday: Sports Day 10/12 is newer, but a holiday no
    # calendar types is never a template.
    assert _sources(template, date(2026, 10, 18)) == {"H1011"}
    assert len(template) == 4
    assert (template["service"] == "sunday_holiday").all()
    assert template["delay_min"].isna().all()


def test_a_weekday_after_a_holiday_is_templated_from_a_weekday_not_from_the_holiday():
    # Mon 10/12 is Sports Day, which ran the holiday timetable. Mon 10/19 is an
    # ordinary weekday, so its template is the last weekday, Fri 10/16.
    runs = _timetable(date(2026, 10, 4), date(2026, 10, 17))
    template = template_future_runs(runs, date(2026, 10, 18), horizon_days=7)
    assert _sources(template, date(2026, 10, 19)) == {"W1016"}
    assert _sources(template, date(2026, 10, 24)) == {"S1017"}
    assert _sources(template, date(2026, 10, 25)) == {"H1011"}


def test_a_holiday_on_a_weekday_is_templated_from_the_last_sunday_or_holiday():
    # Tue 11/3 is Culture Day: the last Sunday-or-holiday before origin Sat 10/31
    # is Sun 10/25, not the previous Tuesday.
    runs = _timetable(date(2026, 10, 17), date(2026, 10, 30))
    template = template_future_runs(runs, date(2026, 10, 31), horizon_days=7)
    assert _sources(template, date(2026, 11, 3)) == {"H1025"}
    assert _sources(template, date(2026, 11, 4)) == {"W1030"}


def test_a_target_day_with_no_same_type_day_in_the_lookback_is_skipped_not_matched_by_weekday():
    # A weekday-only agency: Culture Day has no Sunday or holiday to copy, and
    # must not fall back to the previous Tuesday.
    runs = _timetable(date(2026, 10, 17), date(2026, 10, 30), only={"weekday"})
    template = template_future_runs(runs, date(2026, 10, 31), horizon_days=7)
    assert _sources(template, date(2026, 11, 3)) == set()
    assert _sources(template, date(2026, 11, 1)) == set()
    assert _sources(template, date(2026, 11, 2)) == {"W1030"}


def test_template_future_runs_templates_each_agency_from_its_own_history_only():
    # Both agencies are current, but agency 9 ran no Saturday since 10/10: its
    # Saturday copies its own 10/10, never agency 8's fresher 10/17.
    agency_8 = _timetable(date(2026, 10, 5), date(2026, 10, 23))
    agency_9 = _timetable(date(2026, 10, 5), date(2026, 10, 23), agency_id=9, only={"weekday", "sunday_holiday"})
    agency_9 = pd.concat([agency_9, _timetable(date(2026, 10, 10), date(2026, 10, 10), agency_id=9)])
    template = template_future_runs(pd.concat([agency_8, agency_9], ignore_index=True), date(2026, 10, 24))
    by_agency = {int(agency): rows for agency, rows in template.groupby("agency_id")}
    assert _sources(by_agency[8], date(2026, 10, 31)) == {"S1017"}
    assert _sources(by_agency[9], date(2026, 10, 31)) == {"S1010"}


def test_a_same_type_day_older_than_the_lookback_is_never_a_template():
    # The only Saturday is three weeks back: the Saturday target is skipped
    # rather than copied from a timetable that old.
    weekdays = _timetable(date(2026, 10, 12), date(2026, 10, 23), only={"weekday"})
    old_saturday = _timetable(date(2026, 10, 3), date(2026, 10, 3))
    template = template_future_runs(pd.concat([weekdays, old_saturday], ignore_index=True), date(2026, 10, 24))
    assert _sources(template, date(2026, 10, 31)) == set()
    assert _sources(template, date(2026, 10, 26)) == {"W1023"}


def test_template_future_runs_does_not_template_from_the_origins_own_partial_day():
    # At predict time the origin day can already hold a same-day partial row.
    runs = _timetable(date(2026, 10, 8), date(2026, 10, 21))
    today = _timetable(date(2026, 10, 22), date(2026, 10, 22), slots=1)
    template = template_future_runs(pd.concat([runs, today], ignore_index=True), date(2026, 10, 22))
    copied = {source for d in range(23, 30) for source in _sources(template, date(2026, 10, d))}
    assert not any(source.endswith("1022") for source in copied)
    assert _sources(template, date(2026, 10, 29)) == {"W1021"}


def test_an_agency_whose_newest_complete_day_is_more_than_two_days_old_gets_no_template():
    origin = date(2026, 10, 24)
    current = _timetable(date(2026, 10, 8), date(2026, 10, 22))  # newest day origin-2
    stopped = _timetable(date(2026, 10, 1), date(2026, 10, 21), agency_id=9)  # newest day origin-3
    template = template_future_runs(pd.concat([current, stopped], ignore_index=True), origin)
    assert set(template["agency_id"]) == {8}


def test_a_partial_day_is_skipped_for_the_next_most_recent_day_of_its_type():
    # Exactly two Saturdays: Sat 10/24 kept 3 of the usual 8 slots, so the
    # Saturday target copies Sat 10/17. Half the median of both Saturdays (2.75)
    # would not catch it; the partial day must not vote on what is typical.
    runs = _timetable(date(2026, 10, 11), date(2026, 10, 24), slots=8, short={date(2026, 10, 24): 3})
    template = template_future_runs(runs, date(2026, 10, 25), horizon_days=7)
    assert _sources(template, date(2026, 10, 31)) == {"S1017"}
    assert len(template[template["service_date"] == pd.Timestamp(date(2026, 10, 31))]) == 8


def test_a_partial_last_day_is_neither_a_template_nor_the_agencys_newest_complete_day():
    # Wed 10/21 (origin-1) is a fragment and Tue 10/20 (origin-2) is complete:
    # the agency is still current, and weekdays copy 10/20.
    runs = _timetable(date(2026, 10, 1), date(2026, 10, 21), slots=8, short={date(2026, 10, 21): 3})
    template = template_future_runs(runs, date(2026, 10, 22), horizon_days=7)
    assert _sources(template, date(2026, 10, 23)) == {"W1020"}
    assert _sources(template, date(2026, 10, 28)) == {"W1020"}


def test_a_day_that_starts_hours_late_with_few_slots_is_partial():
    # Sat 10/24 has only its last 3 of 8 hours, as when the archive holding a
    # service day's early hours never arrived.
    runs = pd.concat(
        [
            _timetable(date(2026, 10, 11), date(2026, 10, 23), slots=8),
            _timetable(date(2026, 10, 24), date(2026, 10, 24), hours=(11, 12, 13)),
        ],
        ignore_index=True,
    )
    template = template_future_runs(runs, date(2026, 10, 25), horizon_days=7)
    assert _sources(template, date(2026, 10, 31)) == {"S1017"}


def test_a_timetable_cut_that_keeps_its_span_is_not_partial():
    # From Tue 10/13 (origin-9) weekdays run 4 of their 10 hours, from the same
    # first to the same last run; weekends are unchanged. The agency is still
    # current, and weekdays copy the reduced timetable.
    before = _timetable(date(2026, 9, 24), date(2026, 10, 12), slots=10)
    cut = _timetable(date(2026, 10, 13), date(2026, 10, 21), only={"weekday"}, hours=(6, 9, 12, 15))
    weekends = _timetable(date(2026, 10, 13), date(2026, 10, 21), slots=10, only={"saturday", "sunday_holiday"})
    runs = pd.concat([before, cut, weekends], ignore_index=True)
    template = template_future_runs(runs, date(2026, 10, 22), horizon_days=7)
    assert _sources(template, date(2026, 10, 23)) == {"W1021"}
    assert len(template[template["service_date"] == pd.Timestamp(date(2026, 10, 23))]) == 4
    assert _sources(template, date(2026, 10, 24)) == {"S1017"}


def test_a_fragment_as_the_newest_day_does_not_keep_a_stopped_agency_current():
    # Its feed stopped mid-day on 10/21 (origin-1) after nothing since 10/16.
    runs = _timetable(date(2026, 10, 1), date(2026, 10, 16), slots=8)
    fragment = _timetable(date(2026, 10, 21), date(2026, 10, 21), slots=2)
    template = template_future_runs(pd.concat([runs, fragment], ignore_index=True), date(2026, 10, 22))
    assert template.empty


_WEEKDAYS = (True, True, True, True, True, False, False)


def _calendar(origin: date, services, exceptions=(), *, start=date(2026, 10, 1)):
    """An agency's day types over template_span from its calendar: `services`
    are (service_id, Monday-first flags) running from `start` to 2027-03."""
    weekly = [WeeklyService(sid, days, start, date(2027, 3, 31)) for sid, days in services]
    first, last = template_span(origin)
    return resolve_day_types(first, last, weekly, exceptions, {sid: 10 for sid, _days in services})


def test_the_feeds_calendar_types_a_holiday_it_runs_as_a_weekday():
    # This feed runs its weekday timetable on Culture Day, as its calendar says.
    origin = date(2026, 10, 31)
    types = _calendar(origin, [("wk", _WEEKDAYS), ("su", (False,) * 6 + (True,))])
    runs = _timetable(date(2026, 10, 17), date(2026, 10, 30))
    template = template_future_runs(runs, origin, day_types={8: types})
    assert _sources(template, date(2026, 11, 3)) == {"W1030"}


def test_the_feeds_calendar_types_a_year_end_weekday_it_runs_as_a_holiday():
    # The calendar swaps Tue 12/29's weekday service for the Sunday one, which
    # the holiday rule alone cannot know.
    origin = date(2026, 12, 26)
    exceptions = [("su", date(2026, 12, 29), 1), ("wk", date(2026, 12, 29), 2)]
    types = _calendar(origin, [("wk", _WEEKDAYS), ("su", (False,) * 6 + (True,))], exceptions)
    runs = _timetable(date(2026, 12, 12), date(2026, 12, 25))
    template = template_future_runs(runs, origin, day_types={8: types})
    assert _sources(template, date(2026, 12, 29)) == {"H1220"}
    assert _sources(template, date(2026, 12, 30)) == {"W1225"}


def test_a_feed_with_one_weekend_timetable_copies_a_saturday_from_the_newer_sunday():
    origin = date(2026, 10, 27)
    types = _calendar(origin, [("wk", _WEEKDAYS), ("we", (False,) * 5 + (True, True))])
    runs = _timetable(date(2026, 10, 13), date(2026, 10, 26))
    template = template_future_runs(runs, origin, day_types={8: types})
    assert _sources(template, date(2026, 10, 31)) == {"H1025"}


def test_a_holiday_before_the_loaded_calendar_is_not_a_template():
    # The replica holds only the feed's current calendar, from 10/13. Sports Day
    # 10/12 predates it, so only the holiday rule types it, and this feed ran
    # its weekday timetable that day.
    origin = date(2026, 10, 15)
    types = _calendar(origin, [("wk", _WEEKDAYS), ("su", (False,) * 6 + (True,))], start=date(2026, 10, 13))
    runs = _timetable(date(2026, 10, 1), date(2026, 10, 14), kinds={date(2026, 10, 12): "weekday"})
    template = template_future_runs(runs, origin, day_types={8: types})
    assert _sources(template, date(2026, 10, 18)) == {"H1011"}


def test_a_year_end_day_no_calendar_types_is_not_a_template():
    # Without a calendar, 12/29-1/3 are typed by the weekday rule alone, which
    # cannot know the year-end timetable they ran.
    runs = _timetable(date(2026, 12, 19), date(2027, 1, 1))
    template = template_future_runs(runs, date(2027, 1, 2), horizon_days=7)
    assert _sources(template, date(2027, 1, 4)) == {"W1228"}
    assert _sources(template, date(2027, 1, 3)) == {"H1227"}
