import pytest

from pipeline.query.formatter import (
    _fix,
    format_result,
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


def test_fmt_route_info_empty_rows():
    result = format_result("route_info", [], {"route": "44372"})
    assert "データがありません" in result  # falls back to _no_data()


def test_format_guidance_menu_empty_rows_no_blank():
    # Verify empty ranking yields "(データなし)" not blank line
    # We can test the output string by checking the rendered text without a DB
    # by inspecting the constant structure
    import asyncio

    from pipeline.query.formatter import format_guidance_menu

    async def _run():
        class FakeConn:
            async def fetch(self, sql, *args):
                return []

        return await format_guidance_menu(FakeConn(), 1)

    result = asyncio.run(_run())
    assert "データなし" in result
    # The LLM context menu should not have an empty line between header and menu
    lines = result.split("\n")
    header_idx = next(i for i, line in enumerate(lines) if "遅延ランキング上位10系統" in line)
    # Line immediately after header should not be empty
    assert lines[header_idx + 1].strip() != ""


def test_dow_label_int_maps_to_japanese_char():
    from pipeline.query.formatter import _dow_label
    assert _dow_label(1) == "月"
    assert _dow_label(7) == "日"


def test_dow_label_string_passes_through():
    from pipeline.query.formatter import _dow_label
    assert _dow_label("平日") == "平日"
    assert _dow_label("月") == "月"


def test_dow_label_unknown_int_falls_back_to_str():
    from pipeline.query.formatter import _dow_label
    assert _dow_label(99) == "99"
