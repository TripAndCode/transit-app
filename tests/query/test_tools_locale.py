"""Locale plumbing for pipeline.query.tools.

Covers two surfaces:
  * :func:`_summary` template-table lookup (EN + JP fallback)
  * :func:`dispatch` with an unsupported tool name (no DB needed)
  * :func:`render_tool_result` table/series rendering in EN

These are deliberately offline so they run regardless of Postgres
availability — the DB-driven handler paths are exercised by the live
integration tests in tests/test_tool_queries.py.
"""

import asyncio

import pytest

from pipeline.query.tools import (
    JSON_MODE_ADDENDUM,
    SYSTEM_PROMPT,
    TOOLS,
    ToolResult,
    _summary,
    dispatch,
    render_tool_result,
)


# Override the session-scoped DB fixture — pure-Python tests, no DB needed.
@pytest.fixture(scope="session", autouse=True)
def apply_schema():
    yield


def test_summary_returns_en_when_locale_en():
    """English template wins when locale='en' and an EN row exists."""
    assert _summary("no_data", lang="en") == "No data available."


def test_summary_falls_back_to_ja_when_en_missing():
    """An unknown template short-circuits to the template name itself."""
    # ``not_a_template`` has no row in either locale → returns the literal key.
    assert _summary("not_a_template", lang="en") == "not_a_template"


def test_summary_interpolates_vars():
    out = _summary("ranking_summary", lang="en", label="Delay", count=10)
    assert out == "Delay ranking, top 10 routes"


def test_dispatch_unknown_tool_returns_en_message():
    """The dispatcher's bottom-of-the-funnel error is locale-aware too."""
    result = asyncio.run(dispatch("nonexistent_tool", {}, ctx=None, conn=None, agency_id=1, locale="en"))
    assert result.kind == "empty"
    assert "Unsupported tool" in result.summary
    assert "nonexistent_tool" in result.summary


def test_dispatch_unknown_tool_defaults_to_ja():
    """Locale defaults to JP so legacy callers stay on the previous behaviour."""
    result = asyncio.run(dispatch("nonexistent_tool", {}, ctx=None, conn=None, agency_id=1))
    assert "未対応のツール" in result.summary


def test_render_tool_result_table_en():
    """``render_tool_result(..., locale='en')`` switches the trailing
    'more rows' line to English."""
    result = ToolResult(
        kind="table",
        summary="Delay ranking",
        rows=[[f"r{i}", "weekday", 1.0, 1, 1, 1] for i in range(35)],
        columns=["route_code", "service_type", "avg_min", "p50", "p90", "samples"],
    )
    out = render_tool_result(result, locale="en")
    assert "【Delay ranking】" in out
    assert "5 more" in out  # 35 - 30 = 5 hidden rows


def test_render_tool_result_series_en():
    """Series rendering uses the EN 'mean / samples / worst' decorations."""
    result = ToolResult(
        kind="series",
        summary="Daily trend (2025-01-01 to 2025-01-02): mean 1.50 min",
        series=[
            {
                "date": "2025-01-01",
                "avg_min": 1.5,
                "samples": 100,
                "top_offenders": [{"route_code": "16071"}, {"route_code": "22171"}],
            },
        ],
    )
    out = render_tool_result(result, locale="en")
    assert "mean 1.50 min" in out
    assert "100 samples" in out
    assert "worst:" in out
    assert "route 16071" in out


def test_every_tool_is_documented_in_system_prompt():
    """Every tool in TOOLS must be named in SYSTEM_PROMPT's '利用可能なツール'
    listing, or the LLM has no way to know it exists when calling in native
    tool_calls mode (it only sees TOOLS' JSON schemas indirectly through the
    provider's function-calling machinery, but this app's SYSTEM_PROMPT also
    explicitly enumerates + gives examples for each tool — a tool missing
    from that listing was found, in practice, to make native tool-calling
    for it measurably less reliable, even though the tool's JSON schema in
    TOOLS was itself complete and correct). Regression guard for exactly the
    class of bug where a new tool is registered in TOOLS/_HANDLERS but the
    prompt describing the tool surface to the model is never updated."""
    tool_names = {t["function"]["name"] for t in TOOLS}
    missing = {name for name in tool_names if name not in SYSTEM_PROMPT}
    assert not missing, f"tools missing from SYSTEM_PROMPT: {missing}"


def test_json_mode_addendum_is_not_baked_into_system_prompt():
    """JSON_MODE_ADDENDUM must stay a separate constant, appended to the
    prompt only for the JSON-mode (intent-cache) request in
    pipeline.query.chat — never unconditionally part of SYSTEM_PROMPT, which
    is also used for the native tool_calls request. Reproduced
    deterministically (temperature=0) that leaking this into the native
    tool_calls prompt made the model echo the JSON-mode shape as plain
    message content instead of issuing a real tool_calls entry, for some
    tools. Regression guard for that specific fix."""
    assert JSON_MODE_ADDENDUM not in SYSTEM_PROMPT
