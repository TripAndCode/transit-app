"""Pure unit tests for the day-of-week expected-delay summarizer (DB-free)."""

from pipeline.reports.forecast import summarize_expected_delay_dow


def _rows(*pairs):  # (dow, avg_min, samples)
    return [{"dow": d, "avg_min": a, "samples": s} for (d, a, s) in pairs]


def test_full_7_grid_always_returned():
    out = summarize_expected_delay_dow(_rows((1, 5.0, 100)), "R1")
    assert [d["dow"] for d in out["days"]] == [1, 2, 3, 4, 5, 6, 7]
    mon = next(d for d in out["days"] if d["dow"] == 1)
    assert mon["expected_avg_min"] == 5.0
    assert mon["samples"] == 100
    assert mon["low_confidence"] is False


def test_missing_dow_is_null_zero():
    out = summarize_expected_delay_dow(_rows((1, 5.0, 100)), "R1")
    tue = next(d for d in out["days"] if d["dow"] == 2)
    assert tue["expected_avg_min"] is None
    assert tue["samples"] == 0
    assert tue["low_confidence"] is False


def test_low_confidence_boundary():
    out = summarize_expected_delay_dow(_rows((3, 2.0, 29), (4, 2.0, 30)), "R1")
    assert next(d for d in out["days"] if d["dow"] == 3)["low_confidence"] is True
    assert next(d for d in out["days"] if d["dow"] == 4)["low_confidence"] is False


def test_null_avg_rows_skipped():
    out = summarize_expected_delay_dow(_rows((1, None, 100), (2, 3.0, 0)), "R1")
    assert all(d["expected_avg_min"] is None and d["samples"] == 0 for d in out["days"])


def test_disclaimer_present_both_locales_no_jargon():
    for loc in ("ja", "en"):
        out = summarize_expected_delay_dow(_rows((1, 5.0, 100)), "R1", locale=loc)
        assert isinstance(out["disclaimer"], str) and out["disclaimer"]
        assert "p90" not in out["disclaimer"].lower()
        assert "percentile" not in out["disclaimer"].lower()


def test_route_echoed():
    assert summarize_expected_delay_dow(_rows((1, 5.0, 100)), "R9")["route"] == "R9"
