"""The overview's hour profile covers every hour its rows have, the hours a
service day runs past midnight included (DB-free)."""

from pipeline.reports.overview import _peak_from_hour_rows


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
