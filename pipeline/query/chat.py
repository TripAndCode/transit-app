"""Tool-use chat orchestration for the v2 Ask tab.

Single entry point :func:`chat_with_tools` — sends the user's question to
Groq with the v2 tool surface and either dispatches a tool call to
Postgres or returns the model's free-form refusal text. Out-of-scope
questions (weather, fares, etc.) come back as friendly natural-language
suggestions instead of failing.

Localisation
------------
The orchestrator threads a ``locale`` (``"ja"`` or ``"en"``) from the
HTTP layer through to:
  * the per-call system addendum that instructs the model which language
    to reply in (the static :data:`SYSTEM_PROMPT` stays JP because that
    is the operator-facing instruction set; the addendum overrides the
    output language without re-translating the rules);
  * the user-message prelude that scopes the query to a date range, DOW
    and time band;
  * fallback / error strings rendered when the model errors or refuses;
  * the downstream :func:`dispatch` call which threads the same locale
    through every tool handler and the result summary.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os

from api.range import RangeCtx
from pipeline.query.tools import (
    LOCALE_LANGUAGE_NAME,
    SYSTEM_PROMPT,
    TOOLS,
    ToolResult,
    _summary,
    dispatch,
    render_tool_result,
)

_log = logging.getLogger(__name__)
_groq_client = None

# Extra per-call localisation strings. Keyed identically to the table in
# tools.py — they live here only because they're chat-flow specific
# (user prelude, refusal placeholder, error wrappers) and don't belong
# on the tool surface.
_CHAT_STRINGS = {
    ("locale_instruction", "ja"): (
        "Reply in 日本語 unless the user explicitly asks in another language. "
        "Keep system rules from the previous message intact."
    ),
    ("locale_instruction", "en"): (
        "Reply in English unless the user explicitly asks in another language. "
        "Keep system rules from the previous message intact."
    ),
    ("user_prelude", "ja"): "期間: {from_date}〜{to_date} DOW={dow} time_band={time_band}\n質問: {question}",
    ("user_prelude", "en"): "Range: {from_date} to {to_date} DOW={dow} time_band={time_band}\nQuestion: {question}",
    ("service_unreachable", "ja"): "AI サービスに接続できませんでした。後ほど再試行してください。",
    ("service_unreachable", "en"): "Could not reach the AI service. Please retry later.",
    ("refusal_fallback", "ja"): "ご質問の内容を理解できませんでした。",
    ("refusal_fallback", "en"): "I couldn't understand your question.",
    ("tool_error", "ja"): "ツール {name} の実行中にエラーが発生しました: {exc}",
    ("tool_error", "en"): "Error while running tool {name}: {exc}",
}


def _chat_str(template: str, locale: str, **vars) -> str:
    """Pick a chat-flow string from :data:`_CHAT_STRINGS`, JP-fallback."""
    if locale not in ("ja", "en"):
        locale = "ja"
    tpl = _CHAT_STRINGS.get((template, locale)) or _CHAT_STRINGS.get((template, "ja"), template)
    return tpl.format(**vars) if vars else tpl


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
    locale: str = "ja",
) -> dict:
    """Run one round-trip Ask flow.

    Returns ``{ answer: str, tool_call: {name, args} | None, result: ToolResult | None }``.
    The ``answer`` is what the assistant bubble displays; ``result`` is a
    structured payload the frontend can use for richer rendering (charts,
    tables) when present. ``locale`` ∈ {``"ja"``, ``"en"``} chooses the
    user-facing language across the entire flow.
    """
    client = _get_client()
    language_name = LOCALE_LANGUAGE_NAME.get(locale, LOCALE_LANGUAGE_NAME["ja"])
    locale_addendum = f"Respond in {language_name}. " + _chat_str("locale_instruction", locale)
    user_prelude = _chat_str(
        "user_prelude",
        locale,
        from_date=ctx.from_date,
        to_date=ctx.to_date,
        dow=ctx.dow,
        time_band=ctx.time_band,
        question=question,
    )

    def _sync():
        try:
            resp = client.chat.completions.create(
                model=model,
                messages=[
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "system", "content": locale_addendum},
                    {"role": "user", "content": user_prelude},
                ],
                tools=TOOLS,
                tool_choice="auto",
                temperature=0,
            )
            return resp.choices[0].message
        except Exception as exc:
            _log.warning("Groq chat call failed (%s): %r", exc.__class__.__name__, exc)
            return None

    msg = await asyncio.to_thread(_sync)
    if msg is None:
        return {
            "answer": _chat_str("service_unreachable", locale),
            "tool_call": None,
            "result": None,
        }

    tool_calls = getattr(msg, "tool_calls", None)
    if not tool_calls:
        # Out-of-scope path: model returned plain text (refusal + suggestions).
        text = (msg.content or "").strip() or _chat_str("refusal_fallback", locale)
        return {"answer": text, "tool_call": None, "result": None}

    if len(tool_calls) > 1:
        _log.info("LLM emitted %d tool calls; using the first only", len(tool_calls))
    call = tool_calls[0]
    name = call.function.name
    try:
        args = json.loads(call.function.arguments or "{}")
    except (json.JSONDecodeError, TypeError) as exc:
        _log.warning(
            "Bad tool args from LLM (tool=%s): %s — raw=%r",
            name,
            exc,
            call.function.arguments,
        )
        args = {}
    # Some LLMs occasionally hand back ``arguments`` as a non-object JSON
    # value — null (None after json.loads), a string ('"foo"'), or a list
    # ('[]'). All of these would crash the downstream ``args.get(...)``
    # calls in the tool handlers. Normalise everything that isn't a dict
    # to ``{}`` so the handler still runs (likely returning a "missing
    # required arg" empty result, which is the correct UX).
    if not isinstance(args, dict):
        args = {}

    try:
        result: ToolResult = await dispatch(name, args, ctx, conn, agency_id, locale=locale)
    except Exception as exc:
        _log.exception("Tool %s failed", name)
        return {
            "answer": _chat_str("tool_error", locale, name=name, exc=exc),
            "tool_call": {"name": name, "arguments": args},
            "result": None,
        }

    return {
        "answer": render_tool_result(result, locale=locale),
        "tool_call": {"name": name, "arguments": args},
        "result": _result_to_dict(result),
    }


def _result_to_dict(r: ToolResult) -> dict:
    """Serialize a ToolResult for the JSON response."""
    return {
        "kind": r.kind,
        "summary": r.summary,
        "rows": r.rows,
        "columns": r.columns,
        "series": r.series,
        "pairs": r.pairs,
    }


# Re-export the locale lookup helper so tests / shared code can use the
# same fallback semantics without reaching into the tools module's
# private namespace.
__all__ = ["chat_with_tools", "_summary"]
