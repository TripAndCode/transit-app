"""Tool-use chat orchestration for the v2 Ask tab.

Single entry point :func:`chat_with_tools` — sends the user's question to
Groq with the v2 tool surface and either dispatches a tool call to
Postgres or returns the model's free-form refusal text. Out-of-scope
questions (weather, fares, etc.) come back as friendly natural-language
suggestions instead of failing.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os

from api.range import RangeCtx
from pipeline.query.tools import SYSTEM_PROMPT, TOOLS, ToolResult, dispatch, render_tool_result

_log = logging.getLogger(__name__)
_groq_client = None


def _get_client():
    global _groq_client
    if _groq_client is None:
        from groq import Groq

        _groq_client = Groq(api_key=os.environ["GROQ_API_KEY"])
    return _groq_client


def _reset_client_for_tests() -> None:
    """Reset the singleton — used in tests via monkeypatch."""
    global _groq_client
    _groq_client = None


async def chat_with_tools(
    question: str,
    ctx: RangeCtx,
    conn,
    agency_id: int,
    model: str = "llama-3.3-70b-versatile",
) -> dict:
    """Run one round-trip Ask flow.

    Returns ``{ answer: str, tool_call: {name, args} | None, result: ToolResult | None }``.
    The ``answer`` is what the assistant bubble displays; ``result`` is a
    structured payload the frontend can use for richer rendering (charts,
    tables) when present.
    """
    client = _get_client()

    def _sync():
        try:
            resp = client.chat.completions.create(
                model=model,
                messages=[
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {
                        "role": "user",
                        "content": (
                            f"期間: {ctx.from_date}〜{ctx.to_date} "
                            f"DOW={ctx.dow} time_band={ctx.time_band}\n"
                            f"質問: {question}"
                        ),
                    },
                ],
                tools=TOOLS,
                tool_choice="auto",
                temperature=0,
            )
            return resp.choices[0].message
        except Exception as exc:
            _log.warning("Groq chat call failed: %s", exc)
            return None

    msg = await asyncio.to_thread(_sync)
    if msg is None:
        return {
            "answer": "AI サービスに接続できませんでした。後ほど再試行してください。",
            "tool_call": None,
            "result": None,
        }

    tool_calls = getattr(msg, "tool_calls", None)
    if not tool_calls:
        # Out-of-scope path: model returned plain text (refusal + suggestions).
        text = (msg.content or "").strip() or "ご質問の内容を理解できませんでした。"
        return {"answer": text, "tool_call": None, "result": None}

    call = tool_calls[0]
    name = call.function.name
    try:
        args = json.loads(call.function.arguments or "{}")
    except (json.JSONDecodeError, TypeError):
        args = {}

    try:
        result: ToolResult = await dispatch(name, args, ctx, conn, agency_id)
    except Exception as exc:
        _log.exception("Tool %s failed", name)
        return {
            "answer": f"ツール {name} の実行中にエラーが発生しました: {exc}",
            "tool_call": {"name": name, "arguments": args},
            "result": None,
        }

    return {
        "answer": render_tool_result(result),
        "tool_call": {"name": name, "arguments": args},
        "result": _result_to_dict(result),
    }


def _result_to_dict(r: ToolResult) -> dict:
    """Serialize a ToolResult for the JSON response."""
    return {
        "kind": r.kind,
        "summary_jp": r.summary_jp,
        "rows": r.rows,
        "columns": r.columns,
        "series": r.series,
        "pairs": r.pairs,
    }
