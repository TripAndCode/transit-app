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
    """A route name like 'W53' reads as the number 53 to the numeric guard,
    so a labelled route's name is blanked from the answer before the check."""
    reply = "42052はW53 沖館・新田線（西部営業所→野木和団地）で、定時率は50.6%です。"
    answer, err, guard = await _answer(
        monkeypatch,
        reply,
        context_result=_ON_TIME_RESULT,
        route_labels={"42052": "W53 沖館・新田線 (西部営業所 → 野木和団地)"},
    )
    assert (answer, err, guard) == (reply, None, False)


async def test_the_same_reply_without_route_labels_is_replaced(monkeypatch):
    """With no route_labels, 'W53's embedded '53' has nothing to trace to."""
    reply = "42052はW53 沖館・新田線（西部営業所→野木和団地）で、定時率は50.6%です。"
    answer, err, guard = await _answer(
        monkeypatch,
        reply,
        context_result=_ON_TIME_RESULT,
        route_labels=None,
    )
    assert (answer, err, guard) == (_summary("numeric_guard_fallback", lang="ja"), None, True)


_NUMERIC_NAMED = {"2300128744": "25 草津線 (広島駅 → 草津)"}
_NUMBER_ONLY = {"42052": "7 (野木和団地 → 駅西口)"}


@pytest.mark.parametrize(
    ("labels", "reply"),
    [
        (_NUMERIC_NAMED, "25 草津線の定時率は50.6%です。"),
        (_NUMERIC_NAMED, "25系統の定時率は50.6%です。"),
        (_NUMERIC_NAMED, "Route 25 is 50.6% on time."),
        (_NUMERIC_NAMED, "２５系統の定時率は50.6%です。"),
        ({"2300128744": "２５ 草津線"}, "25系統の定時率は50.6%です。"),
        (_NUMBER_ONLY, "7（野木和団地→駅西口）の定時率は50.6%です。"),
        (_NUMBER_ONLY, "この路線の名前は7です。定時率は50.6%です。"),
        (_NUMBER_ONLY, "The route's name is 7, and it is 50.6% on time."),
        ({"42052": "W53 沖館・新田線"}, "w53 沖館・新田線の定時率は50.6%です。"),
    ],
)
async def test_a_numeric_route_name_passes_where_it_names_the_route(monkeypatch, labels, reply):
    answer, err, guard = await _answer(monkeypatch, reply, context_result=_ON_TIME_RESULT, route_labels=labels)
    assert (answer, err, guard) == (reply, None, False)


@pytest.mark.parametrize(
    ("labels", "reply"),
    [
        (_NUMERIC_NAMED, "25 草津線は平均25分遅れています。"),
        ({"42052": "W53 沖館・新田線 (西部営業所 → 野木和団地)"}, "W53 沖館・新田線は平均53分遅れています。"),
        (_NUMBER_ONLY, "7（野木和団地→駅西口）は平均7分遅れています。"),
        (_NUMBER_ONLY, "この路線は7本遅れています。"),
        (_NUMBER_ONLY, "On this route 7 trips ran late."),
        (_NUMBER_ONLY, "The driver called 7 times about delays."),
        (_NUMBER_ONLY, "路線名は7本あります。"),
    ],
)
async def test_a_route_name_does_not_license_its_digits_elsewhere(monkeypatch, labels, reply):
    answer, err, guard = await _answer(monkeypatch, reply, context_result=_ON_TIME_RESULT, route_labels=labels)
    assert (answer, err, guard) == (_summary("numeric_guard_fallback", lang="ja"), None, True)


@pytest.mark.parametrize(
    ("labels", "on_time_pct", "reply", "replaced"),
    [
        (_NUMBER_ONLY, 7.5, "On this route 7.5% of trips ran on time.", False),
        ({"42052": "5 草津線"}, 7.5, "定時率は7.5（草津線）です。", False),
        (_NUMBER_ONLY, 9, "Route 7.9% of its trips were late.", True),
        ({"42052": "5 草津線"}, 2.4, "定時率は2.5系統平均です。", True),
        ({"42052": "1"}, 1250, "On this route 1,250 trips ran on time.", False),
        ({"42052": "1"}, 250, "On this route 1,250 trips ran on time.", True),
        ({"42052": "250 草津線"}, 1, "1,250系統の平均です。", True),
        ({"42052": "W53"}, 5, "W53-5分の遅延です。", False),
        ({"42052": "25 草津線"}, 5, "25 草津線-5分の遅延です。", False),
        ({"42052": "W53"}, -5, "W53-5分の遅延です。", True),
    ],
)
async def test_a_route_name_never_matches_part_of_a_number(monkeypatch, labels, on_time_pct, reply, replaced):
    result = {**_ON_TIME_RESULT, "rows": [["42052", on_time_pct]]}
    answer, err, guard = await _answer(monkeypatch, reply, context_result=result, route_labels=labels)
    expected = _summary("numeric_guard_fallback", lang="ja") if replaced else reply
    assert (answer, err, guard) == (expected, None, replaced)


@pytest.mark.parametrize(
    "reply",
    [
        "１．港通り線の定時率は50.6%です\n２．丘陵線は42052です",
        "1）港通り線は50.6%\n2）丘陵線も同じです",
        "①W53 沖館・新田線の定時率は50.6%です。②ほかの路線はありません。",
        "W53 沖館・新田線の定時率は５０.６％です。",
    ],
)
async def test_route_labels_leave_list_markers_and_full_width_numbers_to_the_guard(monkeypatch, reply):
    labels = {"42052": "W53 沖館・新田線 (西部営業所 → 野木和団地)"}
    answer, err, guard = await _answer(monkeypatch, reply, context_result=_ON_TIME_RESULT, route_labels=labels)
    assert (answer, err, guard) == (reply, None, False)
