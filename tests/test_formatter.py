"""Tests for pipeline/query/formatter.py.

After the post-/query-retire trim, formatter.py renders only the 5
query_types that api/routers/reports.py dispatches:
ranking / on_time / worst_5min / compare_ranking / dow_ranking.

Label-helper coverage (dow_label / time_label) lives in
tests/test_labels.py.
"""

import pytest

from pipeline.query.formatter import format_result


# Override the session-scoped DB fixture — pure-Python tests, no DB needed.
@pytest.fixture(scope="session", autouse=True)
def apply_schema():
    yield


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
