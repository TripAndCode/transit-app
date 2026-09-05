"""Wiring test for the numeric-claim verifier
(:func:`pipeline.query.hallucination_guard.verify_numeric_claims`) into
``chat_with_tools``'s LLM-authored-free-text return sites.

Reading ``chat_with_tools`` end to end (required before writing this test —
see item 78's own instruction) shows every tool-call result is rendered by
``render_tool_result`` from the dispatched :class:`~pipeline.query.results.ToolResult`
— deterministic formatting of already-grounded data, never LLM-authored
prose, so those return sites are correctly excluded from the guard (mirrors
the plan's own "not `_dispatch_and_respond`'s already-grounded tool-formatted
output" instruction). The one return site where ``answer`` really is raw LLM
free text is the out-of-scope refusal/suggestion path (``tool_calls`` empty,
non-empty ``msg.content``) — and that site has no dispatched tool result to
ground against, so :func:`chat._numeric_guard` is written to treat
``grounding=None`` as "nothing to hallucinate a number away from" and pass
the text through unchanged (see that helper's docstring); the plan's own
sketch assumed a tool result would be available there, which the real control
flow doesn't provide, hence testing the helper's accept/reject logic directly
here in addition to the one live call site.
"""

import os
from datetime import date
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from api.range import RangeCtx
from pipeline.query import chat
from pipeline.query.results import ToolResult

DATABASE_URL = os.environ["DATABASE_URL"]


def _ctx() -> RangeCtx:
    return RangeCtx(from_date=date(2026, 5, 1), to_date=date(2026, 5, 26))


def _fake_tool_call_message(tool_name: str = "describe_data", arguments: str = '{"kind": "routes"}'):
    """Minimal object shape mirroring the Groq/openai SDK's tool_calls response."""
    func = SimpleNamespace(name=tool_name, arguments=arguments)
    call = SimpleNamespace(function=func, id="call_1", type="function")
    return SimpleNamespace(content=None, tool_calls=[call])


def _fake_text_message(text: str | None):
    return SimpleNamespace(content=text, tool_calls=None)


class _FakeClient:
    """Stand-in LLM client that always returns a fixed message."""

    def __init__(self, message):
        self._message = message

    def chat_completions(self, **kwargs):
        return self._message, None


# ─── chat._numeric_guard — direct accept/reject behaviour ────────────────────


def test_numeric_guard_replaces_fabricated_number():
    grounding = {"route": "12", "avg_delay_min": 14.2}
    answer, triggered = chat._numeric_guard("Route 12 is averaging 999.9 minutes late.", grounding, "en")
    assert triggered is True
    assert answer is not None
    assert "999.9" not in answer
    assert answer == chat._summary("numeric_guard_fallback", lang="en")


def test_numeric_guard_passes_grounded_number():
    grounding = {"route": "12", "avg_delay_min": 14.2}
    original = "Route 12 is averaging 14.2 minutes late."
    answer, triggered = chat._numeric_guard(original, grounding, "en")
    assert triggered is False
    assert answer == original


def test_numeric_guard_passes_no_numbers_trivially():
    grounding = {"route": "12", "avg_delay_min": 14.2}
    original = "Delays look typical right now."
    answer, triggered = chat._numeric_guard(original, grounding, "en")
    assert triggered is False
    assert answer == original


def test_numeric_guard_skips_when_no_grounding_available():
    """No tool was dispatched this turn — nothing to hallucinate a number
    away from — so the guard is a deliberate no-op (see the helper's own
    docstring), not a rejection."""
    original = "It's about 999 minutes late."
    answer, triggered = chat._numeric_guard(original, None, "en")
    assert triggered is False
    assert answer == original


def test_numeric_guard_passes_through_empty_answer():
    answer, triggered = chat._numeric_guard("", {"x": 1}, "en")
    assert triggered is False
    assert answer == ""


def test_numeric_guard_uses_localized_fallback_for_japanese():
    grounding = {"avg_delay_min": 14.2}
    answer, triggered = chat._numeric_guard("999.9分遅れています。", grounding, "ja")
    assert triggered is True
    assert answer == chat._summary("numeric_guard_fallback", lang="ja")


# ─── Wired into chat_with_tools's actual return sites ────────────────────────


@pytest.mark.asyncio
async def test_out_of_scope_reply_with_number_is_not_rejected(monkeypatch):
    """The out-of-scope free-text path is the sole LLM-authored-answer site in
    chat_with_tools. Since no tool is dispatched there, grounding is None and
    the guard structurally never rejects — this pins the existing Ask tab's
    out-of-scope refusal/suggestion behaviour as unchanged by this wiring."""
    monkeypatch.setattr(
        chat, "_get_client", lambda: _FakeClient(_fake_text_message("Buses run about every 999 minutes off-peak."))
    )
    out = await chat.chat_with_tools("weather today?", _ctx(), conn=None, agency_id=1, locale="en")
    assert out["success"] is True
    assert out["answer"] == "Buses run about every 999 minutes off-peak."
    assert out["numeric_guard_triggered"] is False


@pytest.mark.asyncio
async def test_empty_out_of_scope_reply_carries_guard_key(monkeypatch):
    monkeypatch.setattr(chat, "_get_client", lambda: _FakeClient(_fake_text_message("")))
    out = await chat.chat_with_tools("質問", _ctx(), conn=None, agency_id=1, locale="ja")
    assert out["success"] is False
    assert out["numeric_guard_triggered"] is False


@pytest.mark.asyncio
async def test_tool_dispatch_success_carries_guard_key_false(monkeypatch):
    """A tool-call result is rendered by render_tool_result — deterministic
    formatting of already-grounded data — and must never be routed through
    the numeric guard even though it contains a real number."""

    async def _fake_dispatch(*a, **k):
        return ToolResult(kind="text", summary="Route 12 delay: 14.2 min")

    monkeypatch.setattr(chat, "_get_client", lambda: _FakeClient(_fake_tool_call_message()))
    monkeypatch.setattr(chat, "dispatch", _fake_dispatch)

    out = await chat.chat_with_tools("route 12 delay?", _ctx(), conn=None, agency_id=1, locale="en")
    assert out["success"] is True
    assert out["numeric_guard_triggered"] is False
    assert out["answer"] == "Route 12 delay: 14.2 min"


@pytest.mark.asyncio
async def test_tool_unavailable_error_carries_guard_key_false(monkeypatch):
    async def _raise_503(*a, **k):
        raise HTTPException(status_code=503, detail="unavailable")

    monkeypatch.setattr(chat, "_get_client", lambda: _FakeClient(_fake_tool_call_message()))
    monkeypatch.setattr(chat, "dispatch", _raise_503)

    out = await chat.chat_with_tools("route 12 delay?", _ctx(), conn=None, agency_id=1, locale="en")
    assert out["success"] is False
    assert out["numeric_guard_triggered"] is False


@pytest.mark.asyncio
async def test_llm_unreachable_carries_guard_key_false(monkeypatch):
    class _DeadClient:
        def chat_completions(self, **kwargs):
            return None, "connection"

    monkeypatch.setattr(chat, "_get_client", lambda: _DeadClient())
    out = await chat.chat_with_tools("質問", _ctx(), conn=None, agency_id=1, locale="ja")
    assert out["success"] is False
    assert out["numeric_guard_triggered"] is False
