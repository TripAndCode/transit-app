"""The overview's hour profile covers every hour its rows have, the hours a
service day runs past midnight included (DB-free)."""

from pipeline.reports.overview import _on_one_hour_axis, _peak_from_hour_rows


def test_a_profile_runs_on_to_the_last_hour_past_midnight():
    out = _peak_from_hour_rows([{"h": 8, "avg_min": 1.0}, {"h": 25, "avg_min": 3.0}])
    assert out is not None
    assert len(out["by_hour"]) == 26
    assert out["by_hour"][25] == 3.0
    assert out["by_hour"][24] is None
    assert (out["peak_hour"], out["peak_avg_min"]) == (25, 3.0)


def test_a_profile_with_no_hour_past_midnight_keeps_24_hours():
    out = _peak_from_hour_rows([{"h": 8, "avg_min": 1.0}])
    assert out is not None
    assert len(out["by_hour"]) == 24


def test_the_profiles_share_one_hour_axis_when_one_runs_past_midnight():
    weekday = _peak_from_hour_rows([{"h": 8, "avg_min": 1.0}, {"h": 24, "avg_min": 2.0}])
    weekend = _peak_from_hour_rows([{"h": 9, "avg_min": 1.0}])
    out = _on_one_hour_axis(weekday, weekend, None)
    assert [len(p["by_hour"]) for p in out[:2]] == [25, 25]
    assert out[1]["by_hour"][24] is None
    assert out[1]["peak_hour"] == 9
    assert out[2] is None
