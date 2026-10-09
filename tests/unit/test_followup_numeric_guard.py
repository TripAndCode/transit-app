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


async def _answer(monkeypatch, reply: str, *, question: str = "一番遅い路線は？", locale: str = "ja"):
    class _Client:
        def chat_completions(self, **kwargs):
            return SimpleNamespace(content=reply), None

    monkeypatch.setattr(followup, "get_client", lambda: _Client())
    return await followup.answer_followup(
        question=question,
        context_tool="top_n",
        context_args={"metric": "avg_delay", "n": 10},
        context_result=_RESULT,
        locale=locale,
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
    assert await _answer(monkeypatch, reply) == (reply, None)


async def test_an_answer_may_repeat_the_questions_own_numbers(monkeypatch):
    reply = "上位2路線は22171と16071です。"
    assert await _answer(monkeypatch, reply, question="上位2路線は？") == (reply, None)


@pytest.mark.parametrize("locale", ["ja", "en"])
async def test_an_answer_with_a_number_the_context_lacks_is_replaced(monkeypatch, locale):
    answer, err = await _answer(monkeypatch, "22171は平均9.8分遅れています。", locale=locale)
    assert (answer, err) == (_summary("numeric_guard_fallback", lang=locale), None)
