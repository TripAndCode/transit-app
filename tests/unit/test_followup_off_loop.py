"""The follow-up's provider call runs in a worker thread.

``LLMClient.chat_completions`` is a blocking network round-trip; awaited
inline from the request handler it would stall every other request on the
worker for as long as the provider takes.
"""

from __future__ import annotations

import threading
from types import SimpleNamespace

from pipeline.query import followup


async def test_followup_calls_the_provider_off_the_event_loop(monkeypatch):
    loop_thread = threading.get_ident()
    seen: dict[str, int] = {}

    class _Client:
        def chat_completions(self, **kwargs):
            seen["thread"] = threading.get_ident()
            return SimpleNamespace(content=" 回答 "), None

    monkeypatch.setattr(followup, "get_client", lambda: _Client())

    answer, err = await followup.answer_followup(
        question="一番遅い路線は？",
        context_tool="top_n",
        context_args={},
        context_result=None,
        locale="ja",
    )

    assert (answer, err) == ("回答", None)
    assert seen["thread"] != loop_thread
