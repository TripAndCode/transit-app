import pytest
from pipeline.query.formatter import (
    format_result,
    _fmt_ranking,
    _no_data,
    _fix,
    _FIX_RE,
)


# Override the session-scoped DB fixture so formatter tests run without PostgreSQL
@pytest.fixture(scope="session", autouse=True)
def apply_schema():
    """No-op override: formatter tests are pure Python, no DB needed."""
    yield


def test_fmt_ranking_basic():
    rows = [("49022", "平日", 2.5, 2.1, 5.0, 100)]
    intent = {"route": "49022", "limit": 15}
    result = format_result("ranking", rows, intent)
    assert "49022" in result
    assert "2.5" in result


def test_fmt_ranking_empty():
    result = format_result("ranking", [], {"limit": 15})
    assert "データがありません" in result


def test_format_result_none_rows_returns_static_msg():
    result = format_result("ranking", None, {})
    assert "GTFSスタティック" in result or "load_static" in result


def test_format_result_unknown_type():
    result = format_result("nonexistent_type", [("a",)], {})
    assert "データがありません" in result


def test_fix_replaces_system_english():
    assert _fix("System 44372") == "系統 44372"


def test_fix_replaces_chinese_system():
    assert _fix("系统44372") == "系統44372"


def test_fmt_compare_verdict():
    rows = [("平日", 2.0, 50), ("土日祝", 3.5, 30)]
    intent = {"route": "44372"}
    result = format_result("compare", rows, intent)
    assert "土日祝" in result
    assert "1.5" in result or "判定" in result


def test_fmt_trend_empty():
    result = format_result("trend", [], {})
    assert "トレンド計算" in result


def test_fmt_by_date():
    rows = [("44372", "平日", 2.1, 100)]
    intent = {"date": "2026-04-15"}
    result = format_result("by_date", rows, intent)
    assert "2026-04-15" in result
    assert "44372" in result
