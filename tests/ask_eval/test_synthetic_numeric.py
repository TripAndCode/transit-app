"""Live-LLM numeric-answer regression test (item 23).

``tests/ask_eval/test_baseline.py``'s golden set (and ``scripts/ask_eval.py``'s
CI gate) only check *which tool the LLM called and with what arguments* —
never whether the *number the tool then returned* is actually right. A model
that calls ``route_stats(route='...')`` with exactly the right arguments but
then states a hallucinated or stale-history-anchored average delay in its
prose answer, or a dispatch bug that quietly returns the wrong row, would
pass every existing ask_eval check.

This module closes that gap using item 21's synthetic ground truth
(``tests.fixtures.synthetic_gtfs``): it seeds a throwaway agency with one
named pattern's static GTFS fragment + matching ClickHouse ``updates`` rows,
asks the running Ask tab a natural-language mean-delay question naming that
pattern's route, and asserts the numeric ``avg_min`` the API actually returns
matches the pattern's hand-computed ``expected["agg_route_stats"]["avg_min"]``
— the same single source of truth ``tests/pipeline/test_synthetic_agg_e2e.py``
(item 21) imports, not a number re-derived here.

Unlike ``test_baseline.py`` (which posts to an already-running, externally
started ``uvicorn`` process via ``EVAL_API_BASE``), this module boots
``api.main.app`` in-process via ``httpx.ASGITransport`` — the same pattern
``tests/api/test_api_ask.py`` uses — wired to the throwaway test Postgres
(``pg_conn``/``agency_id``) and ClickHouse (``ch_client``/``ch_async_client``)
fixtures. ``chat_with_tools`` itself is NOT mocked: when ``RUN_LLM_EVAL=1``
and ``GROQ_API_KEY`` are set, the question really is routed through Groq's
live tool-use API exactly like production traffic, because the exact defect
this test guards against (the *model* inventing or misreading a number) is
inside the thing a mock would otherwise paper over — see CLAUDE.md's "mock
the ML embedder unless a test is explicitly slow" guidance; this test is the
explicitly-slow, explicitly-live exception, same tier as ``test_baseline.py``.

The synthetic route codes (``SYN_UNIFORM`` / ``SYN_SPIKE`` / ``SYN_NULLMIX``)
don't match any Stage-1 rule regex in ``pipeline/query/router.py`` (those
rules match generic dataset questions — route lists, date ranges, rankings —
never a specific route code + "average delay"), and the throwaway agency
never gets a RAG index built, so Stage 2 (embedding nearest-neighbour) never
fires either (no ``rag_chunks`` rows to match against). Every question below
is therefore guaranteed to fall through to Stage 3 (the real LLM call), not
silently resolved by a deterministic rule that would make this pass without
ever exercising the model.
"""

from __future__ import annotations

import os

import httpx
import pytest
from httpx import ASGITransport

from tests.conftest import TEST_ORIGIN
from tests.fixtures.synthetic_gtfs import (
    SyntheticPattern,
    insert_pattern_updates,
    load_pattern_static,
    null_delays,
    outlier_spike,
    uniform_delays,
)

# Applied per-function (NOT as a module-level `pytestmark`) to the three
# live-LLM tests below only — this module also carries fast, offline
# assertion-helper tests at the bottom that must always run (no DB, no
# network, no RUN_LLM_EVAL), to prove the numeric check itself isn't
# vacuous. A module-level pytestmark would skip those too.
_requires_groq_key = pytest.mark.requires_groq_key
_requires_llm_eval_flag = pytest.mark.skipif(
    os.environ.get("RUN_LLM_EVAL") != "1",
    reason="RUN_LLM_EVAL=1 not set",
)

# One natural-language mean-delay question per item-21 pattern. Phrased like
# route_stats's own tool description example ("路線5の遅延", "44372はどう?")
# so the model has every reason to pick that tool, not describe_data/top_n.
_QUESTIONS: dict[str, str] = {
    "uniform_delays": "SYN_UNIFORM系統の平均遅延は何分くらいですか？",
    "outlier_spike": "SYN_SPIKE系統の平均遅延は何分くらいですか？",
    "null_delays": "SYN_NULLMIX系統の平均遅延は何分くらいですか？",
}


def _extract_avg_min(response_json: dict, route_code: str, service_type: str) -> float | None:
    """Pull the ``avg_min`` value for *(route_code, service_type)* out of an
    ``/ask`` response's ``result.rows``/``result.columns`` — the shape
    ``pipeline.query.tools._tool_route_stats`` produces (columns
    ``["route_code", "service_type", "dow", "avg_min", "samples"]``).

    Returns ``None`` when there's no matching row at all (wrong tool called,
    tool returned empty, or the route/service_type didn't match) — the
    caller turns that into a clear assertion message rather than a raw
    ``TypeError`` from indexing a missing column.
    """
    result = response_json.get("result") or {}
    columns = result.get("columns") or []
    rows = result.get("rows") or []
    if "avg_min" not in columns or "route_code" not in columns:
        return None
    idx_avg = columns.index("avg_min")
    idx_route = columns.index("route_code")
    idx_svc = columns.index("service_type") if "service_type" in columns else None
    for row in rows:
        if row[idx_route] != route_code:
            continue
        if idx_svc is not None and row[idx_svc] != service_type:
            continue
        return row[idx_avg]
    return None


def _assert_matches_ground_truth(response_json: dict, pattern: SyntheticPattern, places: int = 2) -> None:
    """Assert the API's numeric answer for *pattern* matches its hand-computed
    ``expected["agg_route_stats"]["avg_min"]`` — the same ground truth
    ``tests/pipeline/test_synthetic_agg_e2e.py`` (item 21) asserts against.

    Checks the tool call name first so a wrong-tool failure reads distinctly
    from a wrong-number failure (both are real defects, but the fix differs).
    """
    tool_call = response_json.get("tool_call") or {}
    assert tool_call.get("name") == "route_stats", (
        f"{pattern.name}: expected tool_call 'route_stats', got {tool_call.get('name')!r} "
        f"(answer: {response_json.get('answer')!r})"
    )
    expected_avg_min = pattern.expected["agg_route_stats"]["avg_min"]
    assert expected_avg_min is not None, f"{pattern.name}: pattern has no comparable avg_min ground truth"
    actual = _extract_avg_min(response_json, pattern.route_code, pattern.service_type)
    assert actual is not None, (
        f"{pattern.name}: no route_stats row for route={pattern.route_code!r} "
        f"service_type={pattern.service_type!r} in response (answer: {response_json.get('answer')!r})"
    )
    assert round(float(actual), places) == expected_avg_min, (
        f"{pattern.name}: API-returned avg_min {actual!r} != ground truth {expected_avg_min!r} "
        f"(answer: {response_json.get('answer')!r})"
    )


async def _ask_about_pattern(
    pattern: SyntheticPattern, tmp_path, pg_conn, agency_id, ch_client, ch_async_client
) -> dict:
    """Seed *pattern* into a throwaway agency and ask the in-process Ask API about it.

    Mirrors ``tests/api/test_api_ask.py``'s ``ask_app``/``ask_client`` wiring
    (module-level ``api.main.app`` with a per-test ``asyncpg`` pool +
    ClickHouse client assigned to ``app.state``), except ``app.state.ch_client``
    is a REAL async ClickHouse client (``ch_async_client``), not ``None`` —
    this test needs the live ``route_stats`` dispatch path, not a mock.
    """
    import asyncpg

    from api.main import app

    load_pattern_static(pattern, tmp_path, agency_id, pg_conn)
    insert_pattern_updates(pattern, ch_client, agency_id)

    pool = await asyncpg.create_pool(os.environ["DATABASE_URL"])
    app.state.pool = pool
    app.state.ch_client = ch_async_client
    try:
        async with httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.post(
                f"/api/{agency_id}/ask",
                json={
                    "question": _QUESTIONS[pattern.name],
                    "ctx": {"from": pattern.date, "to": pattern.date},
                },
                headers={"Origin": TEST_ORIGIN},
            )
        resp.raise_for_status()
        return resp.json()
    finally:
        await pool.close()


@_requires_groq_key
@_requires_llm_eval_flag
async def test_uniform_delays_answer_matches_synthetic_ground_truth(
    tmp_path, pg_conn, agency_id, ch_client, ch_async_client
):
    pattern = uniform_delays()
    response_json = await _ask_about_pattern(pattern, tmp_path, pg_conn, agency_id, ch_client, ch_async_client)
    _assert_matches_ground_truth(response_json, pattern)


@_requires_groq_key
@_requires_llm_eval_flag
async def test_outlier_spike_answer_matches_synthetic_ground_truth(
    tmp_path, pg_conn, agency_id, ch_client, ch_async_client
):
    pattern = outlier_spike()
    response_json = await _ask_about_pattern(pattern, tmp_path, pg_conn, agency_id, ch_client, ch_async_client)
    _assert_matches_ground_truth(response_json, pattern)


@_requires_groq_key
@_requires_llm_eval_flag
async def test_null_delays_answer_matches_synthetic_ground_truth(
    tmp_path, pg_conn, agency_id, ch_client, ch_async_client
):
    pattern = null_delays()
    response_json = await _ask_about_pattern(pattern, tmp_path, pg_conn, agency_id, ch_client, ch_async_client)
    _assert_matches_ground_truth(response_json, pattern)


# ---------------------------------------------------------------------------
# Offline guard: prove `_assert_matches_ground_truth` actually catches a wrong
# number instead of vacuously passing. This needs no DB, no ClickHouse, no
# network, and no RUN_LLM_EVAL — it exercises the assertion helper itself
# against fabricated response payloads, so it always runs (the `_requires_*`
# decorators above are applied per-function, only to the three live-LLM
# tests, not to these).
# ---------------------------------------------------------------------------


def _fake_route_stats_response(pattern: SyntheticPattern, avg_min: float) -> dict:
    return {
        "answer": "テスト",
        "tool_call": {"name": "route_stats", "arguments": {"route": pattern.route_code}},
        "result": {
            "kind": "table",
            "summary": "テスト",
            "rows": [[pattern.route_code, pattern.service_type, "月", avg_min, 25]],
            "columns": ["route_code", "service_type", "dow", "avg_min", "samples"],
            "series": [],
            "pairs": [],
        },
    }


def test_assert_matches_ground_truth_accepts_correct_number():
    pattern = uniform_delays()
    correct = pattern.expected["agg_route_stats"]["avg_min"]
    _assert_matches_ground_truth(_fake_route_stats_response(pattern, correct), pattern)


def test_assert_matches_ground_truth_rejects_wrong_number():
    """Deliberately corrupt the returned avg_min and confirm the check fails —
    guards against this test suite silently passing no matter what number
    comes back (the exact failure mode item 23 exists to catch)."""
    pattern = uniform_delays()
    correct = pattern.expected["agg_route_stats"]["avg_min"]
    wrong = (correct or 0.0) + 100.0
    with pytest.raises(AssertionError, match="!= ground truth"):
        _assert_matches_ground_truth(_fake_route_stats_response(pattern, wrong), pattern)


def test_assert_matches_ground_truth_rejects_wrong_tool():
    pattern = uniform_delays()
    correct = pattern.expected["agg_route_stats"]["avg_min"]
    response_json = _fake_route_stats_response(pattern, correct)
    response_json["tool_call"] = {"name": "describe_data", "arguments": {"kind": "routes"}}
    with pytest.raises(AssertionError, match="expected tool_call 'route_stats'"):
        _assert_matches_ground_truth(response_json, pattern)
