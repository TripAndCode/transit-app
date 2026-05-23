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
