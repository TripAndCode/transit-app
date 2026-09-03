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
is therefore guaranteed to reach Stage 3 (the real LLM call) with the
misleading history below attached, not silently resolved by a deterministic
rule that would make this pass without ever exercising the model.

**Why each question also carries a misleading prior turn**:
``pipeline.query.chat`` only ever lets the LLM pick a ``(tool_name,
arguments)`` pair — the actual
number in a successful tool-call response is rendered deterministically by
``render_tool_result`` (see ``chat.py``'s ``_dispatch_and_respond``), never
composed freely by the model. So a *fresh, isolated* question can't exercise
"the model states a hallucinated number" at all: the model has every
incentive to call the right tool, and once it does, the number is guaranteed
correct by construction. The one path where the model DOES emit free,
unchecked text is when it skips tool dispatch entirely (``tool_calls`` empty,
see ``chat.py``'s ``body = (msg.content or "").strip()`` fallback) — exactly
item 16's original bug shape: an unrelated prior turn's result anchoring the
model into answering from stale context instead of calling a tool. Each
question therefore seeds a ``top_n`` ranking turn (mirroring item 16's own
regression tests, e.g. ``tests/api/test_api_ask.py``) for an unrelated route
before asking about the pattern's own route, so a regression in item 16's
history-scoping guard has something real to trip on:
``assert_matches_ground_truth`` checks the tool-call name first, so a model
that takes the free-text shortcut fails with a clear "expected tool_call
'route_stats', got None" message, distinct from a wrong-number failure.
"""

from __future__ import annotations

import os

import httpx
import pytest
from httpx import ASGITransport

from tests.ask_eval.numeric_ground_truth import assert_matches_ground_truth
from tests.conftest import TEST_ORIGIN
from tests.fixtures.synthetic_gtfs import (
    ALL_PATTERNS,
    SyntheticPattern,
    insert_pattern_updates,
    load_pattern_static,
)

# Applied per-function (NOT as a module-level `pytestmark`) to the live-LLM
# test below only.
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

# A misleading prior turn: an unrelated route-ranking result that has
# nothing to do with the pattern's own route. Mirrors item 16's own live
# repro/regression tests (tests/api/test_api_ask.py's
# test_unrelated_question_with_unrelated_history_gets_fresh_tool_call) — the
# scenario that actually tempts the model to answer from stale context
# instead of dispatching a fresh tool call. See the module docstring's "Why
# each question also carries a misleading prior turn" section for why this
# is necessary.
_MISLEADING_HISTORY: list[dict] = [
    {"question": "遅延ランキングを見せて", "tool": "top_n", "args": {"metric": "avg_delay", "n": 10}},
]


async def _ask_about_pattern(
    pattern: SyntheticPattern, tmp_path, pg_conn, agency_id, ch_client, ch_async_client
) -> dict:
    """Seed *pattern* into a throwaway agency and ask the in-process Ask API
    about it, with a misleading unrelated-route ranking turn already in the
    conversation history (see module docstring for why).

    Mirrors ``tests/api/test_api_ask.py``'s ``ask_app``/``ask_client`` wiring
    (module-level ``api.main.app`` with a per-test ``asyncpg`` pool +
    ClickHouse client assigned to ``app.state``), except ``app.state.ch_client``
    is a REAL async ClickHouse client (``ch_async_client``), not ``None`` —
    this test needs the live ``route_stats`` dispatch path, not a mock.
    """
    import asyncpg

    from api.main import app

    load_pattern_static(pattern, tmp_path, agency_id, pg_conn)
    # `load_static`'s own docstring says "the caller commits" — without this,
    # the running app's separate asyncpg pool never sees these rows (they're
    # invisible under MVCC until committed, then rolled back at fixture
    # teardown), silently making the static seed dead work.
    pg_conn.commit()
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
                    "history": _MISLEADING_HISTORY,
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
@pytest.mark.parametrize("pattern_fn", ALL_PATTERNS)
async def test_answer_matches_synthetic_ground_truth(
    pattern_fn, tmp_path, pg_conn, agency_id, ch_client, ch_async_client
):
    """One test per item-21 pattern in ``ALL_PATTERNS`` — parametrized directly
    over that tuple (not a hand-copied list) so a future pattern added there is
    automatically covered by this LLM-numeric check too, matching the "add a
    pattern, it's covered" framing ``tests/fixtures/synthetic_gtfs.py`` and
    item 21's own ``tests/pipeline/test_synthetic_agg_e2e.py`` already use."""
    pattern = pattern_fn()
    response_json = await _ask_about_pattern(pattern, tmp_path, pg_conn, agency_id, ch_client, ch_async_client)
    assert_matches_ground_truth(response_json, pattern)
