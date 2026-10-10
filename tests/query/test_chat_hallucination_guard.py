"""Which ``chat_with_tools`` return sites carry a numeric-guard verdict.

Every tool-call result is rendered by ``render_tool_result`` from the
dispatched :class:`~pipeline.query.results.ToolResult`, deterministic
formatting of already-grounded data, so the guard has nothing to check there.
The one site where ``answer`` is raw LLM free text, the out-of-scope
refusal/suggestion path (``tool_calls`` empty, non-empty ``msg.content``),
dispatched nothing to ground a number against. Every site therefore reports
``numeric_guard_triggered`` as None, "no verdict", which ``ask_query_log``
keeps distinct from FALSE, "checked and clean". The guard itself runs on the
grounded follow-up (tests/unit/test_followup_numeric_guard.py).
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
    """Minimal object shape mirroring the OpenAI-compatible SDK's tool_calls response."""
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


# ─── Wired into chat_with_tools's actual return sites ────────────────────────


@pytest.mark.asyncio
async def test_out_of_scope_reply_with_number_passes_through(monkeypatch):
    """The out-of-scope free-text path dispatches no tool, so the guard has no
    grounding and gives no verdict. Rejecting on digits there would replace
    the reply SYSTEM_PROMPT asks for, a refusal naming concrete route_codes
    and periods, with a message about numbers."""
    reply = "Buses run about every 999 minutes off-peak."
    monkeypatch.setattr(chat, "_get_client", lambda: _FakeClient(_fake_text_message(reply)))
    out = await chat.chat_with_tools("weather today?", _ctx(), conn=None, agency_id=1, locale="en")
    assert out["success"] is True
    assert out["answer"] == reply
    assert out["numeric_guard_triggered"] is None


@pytest.mark.asyncio
async def test_out_of_scope_reply_without_number_passes_through(monkeypatch):
    """A benign out-of-scope reply with no numeric claims has nothing to
    verify and must pass through unchanged."""
    monkeypatch.setattr(
        chat,
        "_get_client",
        lambda: _FakeClient(_fake_text_message("I can only help with transit questions for this agency.")),
    )
    out = await chat.chat_with_tools("weather today?", _ctx(), conn=None, agency_id=1, locale="en")
    assert out["success"] is True
    assert out["answer"] == "I can only help with transit questions for this agency."
    assert out["numeric_guard_triggered"] is None


@pytest.mark.asyncio
async def test_empty_out_of_scope_reply_carries_guard_key(monkeypatch):
    monkeypatch.setattr(chat, "_get_client", lambda: _FakeClient(_fake_text_message("")))
    out = await chat.chat_with_tools("質問", _ctx(), conn=None, agency_id=1, locale="ja")
    assert out["success"] is False
    assert out["numeric_guard_triggered"] is None


@pytest.mark.asyncio
async def test_tool_dispatch_success_carries_guard_key_none(monkeypatch):
    """A tool-call result is rendered by render_tool_result — deterministic
    formatting of already-grounded data — and must never be routed through
    the numeric guard even though it contains a real number."""

    async def _fake_dispatch(*a, **k):
        return ToolResult(kind="text", summary="Route 12 delay: 14.2 min")

    monkeypatch.setattr(chat, "_get_client", lambda: _FakeClient(_fake_tool_call_message()))
    monkeypatch.setattr(chat, "dispatch", _fake_dispatch)

    out = await chat.chat_with_tools("route 12 delay?", _ctx(), conn=None, agency_id=1, locale="en")
    assert out["success"] is True
    # None, not False: the guard never ran here. FALSE would assert it ran and
    # found nothing wrong, a distinction ask_query_log's column relies on to
    # tell a dormant guard from one that simply never fires.
    assert out["numeric_guard_triggered"] is None
    assert out["answer"] == "Route 12 delay: 14.2 min"


@pytest.mark.asyncio
async def test_tool_unavailable_error_carries_guard_key_none(monkeypatch):
    async def _raise_503(*a, **k):
        raise HTTPException(status_code=503, detail="unavailable")

    monkeypatch.setattr(chat, "_get_client", lambda: _FakeClient(_fake_tool_call_message()))
    monkeypatch.setattr(chat, "dispatch", _raise_503)

    out = await chat.chat_with_tools("route 12 delay?", _ctx(), conn=None, agency_id=1, locale="en")
    assert out["success"] is False
    assert out["numeric_guard_triggered"] is None


@pytest.mark.asyncio
async def test_llm_unreachable_carries_guard_key_none(monkeypatch):
    class _DeadClient:
        def chat_completions(self, **kwargs):
            return None, "connection"

    monkeypatch.setattr(chat, "_get_client", lambda: _DeadClient())
    out = await chat.chat_with_tools("質問", _ctx(), conn=None, agency_id=1, locale="ja")
    assert out["success"] is False
    assert out["numeric_guard_triggered"] is None


@pytest.mark.asyncio
async def test_out_of_scope_reply_naming_a_route_code_passes_through(monkeypatch):
    """SYSTEM_PROMPT rule 2 requires route arguments as 4-5 digit route_codes,
    and its own worked example for an out-of-scope question suggests one.
    Treating every digit on this ungrounded path as a fabrication replaced
    that reply with the numeric fallback, which answers nothing."""
    reply = "このチャットでは天気との比較は扱えません。代わりに『22171の平日と土日祝の比較』が答えられます"
    monkeypatch.setattr(chat, "_get_client", lambda: _FakeClient(_fake_text_message(reply)))
    out = await chat.chat_with_tools("雨天時の比較", _ctx(), conn=None, agency_id=1, locale="ja")
    assert out["answer"] == reply
    assert out["numeric_guard_triggered"] is None
