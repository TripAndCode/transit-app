"""Pure unit tests for the 24-hour expected-delay profile summarizer (DB-free)."""

from pipeline.reports.forecast import summarize_expected_delay_profile


def _rows(*pairs):  # (hour, avg_min, samples)
    return [{"hour": h, "avg_min": a, "samples": s} for (h, a, s) in pairs]


def test_full_24_grid_always_returned():
    out = summarize_expected_delay_profile(_rows((8, 5.0, 100)), "R1", "平日")
    assert [h["hour"] for h in out["hours"]] == list(range(24))
    eight = next(h for h in out["hours"] if h["hour"] == 8)
    assert eight["expected_avg_min"] == 5.0
    assert eight["samples"] == 100
    assert eight["low_confidence"] is False


def test_missing_hour_is_null_zero():
    out = summarize_expected_delay_profile(_rows((8, 5.0, 100)), "R1", "平日")
    nine = next(h for h in out["hours"] if h["hour"] == 9)
    assert nine["expected_avg_min"] is None
    assert nine["samples"] == 0
    assert nine["low_confidence"] is False


def test_low_confidence_boundary():
    out = summarize_expected_delay_profile(_rows((6, 2.0, 29), (7, 2.0, 30)), "R1", "平日")
    six = next(h for h in out["hours"] if h["hour"] == 6)
    seven = next(h for h in out["hours"] if h["hour"] == 7)
    assert six["low_confidence"] is True
    assert seven["low_confidence"] is False


def test_null_avg_rows_skipped():
    out = summarize_expected_delay_profile(_rows((8, None, 100), (9, 3.0, 0)), "R1", "平日")
    assert all(h["expected_avg_min"] is None and h["samples"] == 0 for h in out["hours"])


def test_disclaimer_present_both_locales_no_jargon():
    for loc in ("ja", "en"):
        out = summarize_expected_delay_profile(_rows((8, 5.0, 100)), "R1", "平日", locale=loc)
        assert isinstance(out["disclaimer"], str) and out["disclaimer"]
        assert "p90" not in out["disclaimer"].lower()
        assert "percentile" not in out["disclaimer"].lower()


def test_route_and_service_echoed():
    out = summarize_expected_delay_profile(_rows((8, 5.0, 100)), "R9", "土日祝")
    assert out["route"] == "R9"
    assert out["service_type"] == "土日祝"
