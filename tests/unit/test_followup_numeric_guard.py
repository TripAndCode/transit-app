"""The follow-up answers only from a prior result, so a number in its answer
must trace back to the context the model was shown or to the question."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from pipeline.query import followup
from pipeline.query.tools import _summary

_RESULT = {
    "kind": "table",
    "summary": "遅延ランキング 2件",
    "columns": ["route_code", "avg_min", "samples"],
    "rows": [["22171", 4.25, 310], ["16071", 3.1, 122]],
}


_ON_TIME_RESULT = {
    "kind": "table",
    "summary": "定時率",
    "columns": ["route_code", "on_time_pct"],
    "rows": [["42052", 50.6]],
}


async def _answer(
    monkeypatch,
    reply: str,
    *,
    question: str = "一番遅い路線は？",
    locale: str = "ja",
    context_result: dict = _RESULT,
    route_labels: dict[str, str] | None = None,
):
    class _Client:
        def chat_completions(self, **kwargs):
            return SimpleNamespace(content=reply), None

    monkeypatch.setattr(followup, "get_client", lambda: _Client())
    return await followup.answer_followup(
        question=question,
        context_tool="top_n",
        context_args={"metric": "avg_delay", "n": 10},
        context_result=context_result,
        locale=locale,
        route_labels=route_labels,
    )


@pytest.mark.parametrize(
    "reply",
    [
        "22171が平均4.25分で最も遅く、観測は310件です。",
        "22171がおよそ4分で最も遅れています。",
        "1. 22171（4.25分）\n2. 16071（3.1分）",
    ],
)
async def test_an_answer_quoting_the_context_passes(monkeypatch, reply):
    assert await _answer(monkeypatch, reply) == (reply, None, False)


async def test_an_answer_may_repeat_the_questions_own_numbers(monkeypatch):
    reply = "上位2路線は22171と16071です。"
    assert await _answer(monkeypatch, reply, question="上位2路線は？") == (reply, None, False)


@pytest.mark.parametrize("locale", ["ja", "en"])
async def test_an_answer_with_a_number_the_context_lacks_is_replaced(monkeypatch, locale):
    answer, err, guard = await _answer(monkeypatch, "22171は平均9.8分遅れています。", locale=locale)
    assert (answer, err, guard) == (_summary("numeric_guard_fallback", lang=locale), None, True)


async def test_a_route_name_quoting_its_code_passes_when_route_labels_ground_it(monkeypatch):
    """A route name like 'W53' reads as the number 53 to the numeric guard.
    Without its label in the grounding, that would be indistinguishable from
    an invented number -- with it, the guard traces '53' back to the Routes
    section the same way it traces any other grounded figure."""
    reply = "42052はW53 沖館・新田線（西部営業所→野木和団地）で、定時率は50.6%です。"
    answer, err, guard = await _answer(
        monkeypatch,
        reply,
        context_result=_ON_TIME_RESULT,
        route_labels={"42052": "W53 沖館・新田線 (西部営業所 → 野木和団地)"},
    )
    assert (answer, err, guard) == (reply, None, False)


async def test_the_same_reply_without_route_labels_is_replaced(monkeypatch):
    """Proves the label is what let the reply above through: with no
    route_labels, 'W53's embedded '53' has nothing to trace to."""
    reply = "42052はW53 沖館・新田線（西部営業所→野木和団地）で、定時率は50.6%です。"
    answer, err, guard = await _answer(
        monkeypatch,
        reply,
        context_result=_ON_TIME_RESULT,
        route_labels=None,
    )
    assert (answer, err, guard) == (_summary("numeric_guard_fallback", lang="ja"), None, True)
