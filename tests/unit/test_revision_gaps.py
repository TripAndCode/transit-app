from array import array

from pipeline.reports.revision_gaps import NewGap, changed_departures, departures_of, new_gaps
from pipeline.timetable_sampling import DAY_TYPES

H = 3600


def _secs(*clock: str) -> list[int]:
    out = []
    for hhmm in clock:
        hh, mm = (int(part) for part in hhmm.split(":"))
        out.append(hh * H + mm * 60)
    return out


def test_departures_are_sorted_per_day_type_and_stop():
    rows = [(1, "S1", [8 * H, 7 * H]), (3, "S2", [10 * H])]
    out = departures_of(rows)
    assert list(out["weekday"]["S1"]) == [7 * H, 8 * H]
    assert list(out["sunday_holiday"]["S2"]) == [10 * H]
    assert out["saturday"] == {}


def test_only_stops_whose_departures_differ_on_a_known_day_type_are_kept():
    before = departures_of([(1, "same", _secs("07:00")), (1, "moved", _secs("07:00")), (2, "sat", _secs("09:00"))])
    after = departures_of([(1, "same", _secs("07:00")), (1, "moved", _secs("07:30")), (1, "new", _secs("08:00"))])
    out = changed_departures(before, after, ("weekday",))
    assert set(out["weekday"]) == {"moved", "new"}
    assert list(out["weekday"]["new"][0]) == []
    # Saturday is not known on both sides, so its stop is never compared.
    assert "saturday" not in out


def _gaps(before: dict[str, list[int]], after: dict[str, list[int]], from_hour=10, to_hour=16, threshold_min=60):
    changed = changed_departures(
        departures_of([(1, s, secs) for s, secs in before.items()]),
        departures_of([(1, s, secs) for s, secs in after.items()]),
        ("weekday",),
    )
    return new_gaps(changed, ("weekday",), from_hour, to_hour, threshold_min * 60)


def test_a_gap_that_grows_past_the_threshold_is_new():
    every_half_hour = _secs("10:00", "10:30", "11:00", "11:30", "12:00", "12:30", "13:00", "13:30", "14:00")
    every_half_hour += _secs("14:30", "15:00", "15:30")
    cut = [s for s in every_half_hour if s not in _secs("12:00", "12:30")]
    [gap] = _gaps({"S1": every_half_hour}, {"S1": cut})
    assert gap == NewGap("S1", "weekday", 12, 10, 30 * 60, 90 * 60)


def test_a_gap_exactly_at_the_threshold_is_not_over_it():
    # 10:00-16:00 with departures every hour on the hour: the longest gap is 60 min.
    hourly = _secs("10:00", "11:00", "12:00", "13:00", "14:00", "15:00")
    assert _gaps({"S1": _secs("10:00", "10:30") + hourly[1:]}, {"S1": hourly}) == []
    [gap] = _gaps({"S1": hourly}, {"S1": hourly[:-1]})
    assert (gap.gap_before, gap.gap_after) == (60 * 60, 120 * 60)


def test_a_stop_left_with_no_departure_in_the_band_has_the_whole_band_as_its_gap():
    hourly = _secs("10:00", "11:00", "12:00", "13:00", "14:00", "15:00")
    [gap] = _gaps({"S1": hourly + _secs("18:00")}, {"S1": _secs("18:00")})
    assert (gap.departures_before, gap.departures_after, gap.gap_after) == (6, 0, 6 * H)
    [ended] = _gaps({"S1": hourly}, {})
    assert ended.gap_after == 6 * H


def test_a_stop_only_after_the_revision_or_already_over_before_is_not_new():
    hourly = _secs("10:00", "11:00", "12:00", "13:00", "14:00", "15:00")
    assert _gaps({}, {"S1": hourly}) == []
    assert _gaps({"S1": _secs("10:00", "14:00")}, {"S1": _secs("10:00")}) == []


def test_departures_outside_the_band_do_not_stretch_a_gap():
    # 08:00 and 17:00 lie outside 10-16, so the band's edges bound the gap.
    before = _secs("08:00", "10:00", "11:00", "12:00", "13:00", "14:00", "15:00", "17:00")
    after = _secs("08:00", "11:30", "12:00", "13:00", "14:00", "15:00", "17:00")
    [gap] = _gaps({"S1": before}, {"S1": after})
    assert (gap.gap_before, gap.gap_after) == (60 * 60, 90 * 60)


def test_new_gaps_list_the_longest_after_gap_first_then_by_growth_day_type_and_stop():
    hourly = _secs("10:00", "11:00", "12:00", "13:00", "14:00", "15:00")
    changed = changed_departures(
        departures_of(
            [(1, "A", hourly), (1, "B", hourly), (1, "C", hourly), (3, "A", hourly)],
        ),
        departures_of([(1, "A", hourly[:4]), (1, "B", hourly[:3]), (1, "C", hourly[:4]), (3, "A", hourly[:4])]),
        DAY_TYPES,
    )
    order = [(g.stop_id, g.day_type) for g in new_gaps(changed, DAY_TYPES, 10, 16, 60 * 60)]
    assert order == [("B", "weekday"), ("A", "weekday"), ("C", "weekday"), ("A", "sunday_holiday")]


def test_a_day_type_not_asked_for_is_not_read():
    hourly = _secs("10:00", "11:00", "12:00", "13:00", "14:00", "15:00")
    changed = changed_departures(
        departures_of([(1, "A", hourly), (2, "A", hourly)]),
        departures_of([(1, "A", hourly[:3]), (2, "A", hourly[:3])]),
        DAY_TYPES,
    )
    assert [g.day_type for g in new_gaps(changed, ("saturday",), 10, 16, 60 * 60)] == ["saturday"]


def test_departures_are_kept_compactly():
    out = departures_of([(1, "S1", [7 * H])])
    assert isinstance(out["weekday"]["S1"], array)
