"""Tests for pipeline/query/formatter.py.

Covers the 5 query_types api/routers/reports.py dispatches:
ranking / on_time / worst_5min / compare_ranking / dow_ranking.

Label-helper coverage (dow_label / time_label) lives in
tests/unit/test_labels.py. format_trend_text's NULL-day handling is covered
below, cross-checked against pipeline.reports.rankings._weighted_avg_min
(whose own NULL-skipping behavior is pinned separately in
tests/unit/test_trend_weighted_avg.py).
"""

from datetime import date

from pipeline.query.formatter import format_result, format_trend_text
from pipeline.reports.rankings import _weighted_avg_min


def test_fmt_ranking_basic():
    rows = [("49022", "平日", 2.5, 2.1, 5.0, 100)]
    intent = {"route": "49022", "limit": 15, "service": "平日"}
    result = format_result("ranking", rows, intent)
    assert "49022" in result
    assert "2.5" in result


def test_fmt_ranking_empty():
    result = format_result("ranking", [], {"limit": 15})
    assert "データがありません" in result


def test_format_result_none_rows_returns_static_msg():
    result = format_result("ranking", None, {})
    assert "GTFS" in result or "load_static" in result


def test_format_result_unknown_type():
    result = format_result("nonexistent_type", [("a",)], {})
    assert "データがありません" in result


def test_fmt_dow_ranking_renders_int_dow_as_jp():
    """ISODOW int from compute_dow_ranking renders as a Japanese day char."""
    # Row shape: (route_code, service_type, dow, avg_min, samples)
    rows = [("44372", "平日", 1, 3.5, 100)]  # 1 = Monday in ISODOW
    result = format_result("dow_ranking", rows, {})
    assert "月" in result, f"expected '月' in {result!r}"


def test_fmt_worst_5min_row_shape():
    """compute_worst_5min returns (route_code, service_type, late5_count,
    avg_min, samples). Pin the index mapping to catch the historical
    r[2]/r[3] swap that surfaced on live data."""
    rows = [("14022", "平日", 759, 3.4, 3159)]
    result = format_result("worst_5min", rows, {})
    assert "759回" in result, f"expected '759回' in {result!r}"
    assert "3.4" in result, f"expected '3.4' avg in {result!r}"
    assert "3159件" in result, f"expected '3159件' in {result!r}"


def test_fmt_compare_ranking_signs_direction():
    """Sign of the delta determines the Japanese direction label."""
    # signed > 0 → 土日祝>平日
    pos_rows = [("19042", 2.6, 8.2, 5.6, 5.6)]
    assert "土日祝>平日" in format_result("compare_ranking", pos_rows, {})
    # signed < 0 → 平日>土日祝
    neg_rows = [("56041", 4.1, 0.8, 3.3, -3.3)]
    assert "平日>土日祝" in format_result("compare_ranking", neg_rows, {})


def test_fmt_ranking_no_service_uses_p50_p90_labels():
    """compute_ranking returns (route, service, avg, p50, p90, samples).
    The no-service path was previously mis-labelling p50 as 平日 and p90 as
    土日祝 — pin the corrected label set so a regression is immediate."""
    rows = [("16101", "平日", 7.9, 4.8, 22.8, 152)]
    result = format_result("ranking", rows, {"limit": 100})
    assert "p50=4.8" in result
    assert "p90=22.8" in result
    # The mis-label was "平日{p50}分・土日祝{p90}分" — verify it's gone.
    assert "土日祝22.8" not in result


def test_fmt_on_time_ranking_shape():
    """compute_on_time returns (route, service, on_time_pct, avg, samples)."""
    rows = [("14081", "平日", 76.6, 0.5, 205)]
    result = format_result("on_time", rows, {})
    assert "定時率76.6%" in result
    assert "平均0.5分" in result
    assert "205件" in result


def test_fmt_dow_ranking_weekday_header():
    rows = [("16101", "平日", "平日", 8.8, 114)]
    result = format_result("dow_ranking", rows, {"dow_group": "weekday"})
    assert "【平日遅延ランキング】" in result


def test_fmt_ranking_en_locale_uses_english_strings():
    """locale='en' switches every label/header/row template to English.

    Pins the basic EN ranking shape so a regression on the formatter
    translation table surfaces immediately.
    """
    rows = [("16101", "平日", 7.9, 4.8, 22.8, 152)]
    result = format_result("ranking", rows, {"limit": 100}, locale="en")
    assert "Delay ranking" in result
    assert "top 100 routes" in result
    assert "route 16101" in result
    assert "mean 7.9 min" in result
    assert "p50=4.8" in result
    # Empty-rows path also translates.
    empty = format_result("ranking", [], {"limit": 100}, locale="en")
    assert "No data available" in empty


def test_fmt_dow_ranking_en_translates_iso_dow_and_rollup():
    """EN locale: ISODOW int → short EN name, rollup label → EN word."""
    # ISODOW int path.
    int_rows = [("44372", "平日", 1, 3.5, 100)]
    result = format_result("dow_ranking", int_rows, {}, locale="en")
    assert "Mon" in result, f"expected 'Mon' in {result!r}"
    # Rollup label path with weekday header.
    rollup_rows = [("16101", "平日", "平日", 8.8, 114)]
    result = format_result("dow_ranking", rollup_rows, {"dow_group": "weekday"}, locale="en")
    assert "Weekday delay ranking" in result
    assert "Weekday" in result  # the per-row label too


def test_format_trend_text_empty_days_list():
    result = format_trend_text([], date(2026, 4, 1), date(2026, 4, 3))
    assert "データがありません" in result


def test_format_trend_text_all_null_days_reports_no_data():
    """Every bucket NULL (e.g. none re-analyzed since migration 0028 yet) —
    there is no measured sample anywhere, so this must render the same
    "no data" text as an empty list, not "0.00 min"."""
    days = [
        {"date": "2026-04-01", "avg_min": None, "samples": 0},
        {"date": "2026-04-02", "avg_min": None, "samples": 0},
    ]
    result = format_trend_text(days, date(2026, 4, 1), date(2026, 4, 2))
    assert "データがありません" in result


def test_format_trend_text_skips_null_day_not_zero():
    """A single all-NULL day amid two real days must not drag the mean down
    toward 0, and must not count in the rendered observed-day total."""
    days = [
        {"date": "2026-04-01", "avg_min": None, "samples": 0},
        {"date": "2026-04-02", "avg_min": 2.0, "samples": 1000},
        {"date": "2026-04-03", "avg_min": 10.0, "samples": 5},
    ]
    result = format_trend_text(days, date(2026, 4, 1), date(2026, 4, 3))
    # Old buggy behavior: sum(0 + 2.00 + 10.00) / 3 = 4.00.
    assert "4.00" not in result
    # Correct sample-weighted mean over the two real days only.
    expected_avg = _weighted_avg_min(days)
    assert expected_avg is not None
    assert f"{expected_avg:.2f}" in result
    # Observed-day count excludes the NULL day (2, not 3).
    assert "観測日数: 2日" in result


def test_format_trend_text_matches_weighted_avg_min_helper():
    """format_trend_text's mean must agree with _weighted_avg_min (the same
    sample-weighted, NULL-skipping pooling the Ask tab's time_series tool
    uses on identical compute_trend_series output) to within rounding —
    the two surfaces must not show different headline numbers for the same
    underlying data.
    """
    days = [
        {"date": "2026-04-01", "avg_min": None, "samples": 0},
        {"date": "2026-04-02", "avg_min": 2.0, "samples": 1000},
        {"date": "2026-04-03", "avg_min": 10.0, "samples": 5},
    ]
    result = format_trend_text(days, date(2026, 4, 1), date(2026, 4, 3))
    weighted = _weighted_avg_min(days)
    assert weighted is not None
    # format_trend_text renders avg via "{avg:.2f}" (see _LOCALES["trend_header"]).
    assert f"{weighted:.2f}" in result
