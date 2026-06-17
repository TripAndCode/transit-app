"""Unit tests for the pure expected-delay summarizer (no DB).

Rows reaching the summarizer are already hour-filtered by the endpoint's SQL, so
each row is just an (avg_min, samples) pair for one scheduled departure in the hour.
"""

from pipeline.reports.forecast import summarize_expected_delay


def _row(avg_min, samples):
    return {"avg_min": avg_min, "samples": samples}


def test_sample_weighted_avg():
    rows = [_row(6.0, 100), _row(9.0, 300)]
    out = summarize_expected_delay(rows, "44372", "平日", 17, "ja")
    assert out["samples"] == 400
    assert out["expected_avg_min"] == round((6.0 * 100 + 9.0 * 300) / 400, 1)
    assert out["low_confidence"] is False
    assert out["route"] == "44372" and out["service_type"] == "平日" and out["hour"] == 17
    assert "expected_p90_min" not in out  # p90 deliberately dropped


def test_no_rows_returns_no_data_shape():
    out = summarize_expected_delay([], "44372", "平日", 17, "ja")
    assert out["samples"] == 0
    assert out["expected_avg_min"] is None
    assert out["low_confidence"] is True
    assert "目安を出せません" in out["disclaimer"]


def test_low_confidence_below_30_samples():
    out = summarize_expected_delay([_row(5.0, 29)], "R", "平日", 17, "ja")
    assert out["low_confidence"] is True
    assert "参考値" in out["disclaimer"]
    out30 = summarize_expected_delay([_row(5.0, 30)], "R", "平日", 17, "ja")
    assert out30["low_confidence"] is False


def test_disclaimer_states_sample_count_and_switches_locale():
    out_ja = summarize_expected_delay([_row(5.0, 400)], "R", "平日", 17, "ja")
    out_en = summarize_expected_delay([_row(5.0, 400)], "R", "平日", 17, "en")
    assert "400" in out_ja["disclaimer"]
    assert "400" in out_en["disclaimer"]
    assert out_ja["disclaimer"] != out_en["disclaimer"]
    assert "average" in out_en["disclaimer"].lower()


def test_disclaimer_has_no_jargon():
    out = summarize_expected_delay([_row(5.0, 400)], "R", "平日", 17, "en")
    low = out["disclaimer"].lower()
    assert "p90" not in low and "percentile" not in low and "baseline" not in low


def test_null_avg_or_zero_sample_rows_skipped():
    rows = [_row(None, 100), _row(6.0, 0), _row(6.0, 50)]
    out = summarize_expected_delay(rows, "R", "平日", 17, "ja")
    assert out["samples"] == 50  # only the (6.0, 50) row counts
