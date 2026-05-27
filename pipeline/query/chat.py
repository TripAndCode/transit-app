"""Tool-use chat orchestration for the v2 Ask tab.

Single entry point :func:`chat_with_tools` — sends the user's question
through the provider-agnostic :class:`~pipeline.query.llm_client.LLMClient`
with the v2 tool surface and either dispatches a tool call to Postgres
or returns the model's free-form refusal text. Out-of-scope questions
(weather, fares, etc.) come back as friendly natural-language
suggestions instead of failing.

Provider selection lives in env (``CHAT_PROVIDERS``); this module is
provider-agnostic. The historical default of Groq is preserved when
``CHAT_PROVIDERS`` is unset.

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

from api.range import RangeCtx
from pipeline.query.llm_client import get_client
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
    """Back-compat shim returning the provider-agnostic LLM client.

    Older tests monkeypatch this symbol with a fake Groq-shaped client;
    keep it around so ``monkeypatch.setattr(chat, "_get_client", ...)``
    still works while production code goes through ``llm_client``.
    """
    return get_client()


async def chat_with_tools(
    question: str,
    ctx: RangeCtx,
    conn,
    agency_id: int,
    model: str | None = None,
    locale: str = "ja",
    rag_examples: list | None = None,
    history: list | None = None,
) -> dict:
    """Run one round-trip Ask flow.

    Returns ``{ answer: str, tool_call: {name, args} | None, result: ToolResult | None }``.
    The ``answer`` is what the assistant bubble displays; ``result`` is a
    structured payload the frontend can use for richer rendering (charts,
    tables) when present. ``locale`` ∈ {``"ja"``, ``"en"``} chooses the
    user-facing language across the entire flow.

    Model selection
    ---------------
    The ``model`` parameter is forwarded to the LLM adapter as a
    per-call override. When ``model=None`` (the default), the adapter
    uses each provider's own configured default (``{PROVIDER}_MODEL``
    env var, e.g. ``CEREBRAS_MODEL`` / ``GROQ_MODEL``). Passing a
    vendor-specific model name (e.g. ``"llama-3.3-70b-versatile"``) only
    works if every provider in the fallback ladder accepts it.
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

    # Few-shot block: when the upstream router supplied nearest-neighbour
    # examples from the golden-set RAG index, append them to the system
    # prompt so the model can pattern-match similar questions onto the
    # right tool + arg shape. Kept off the static SYSTEM_PROMPT because
    # the block is per-request and varies with retrieval.
    system_prompt = SYSTEM_PROMPT
    if rag_examples:
        lines = ["\n", "== 類似質問の例 (参考) =="]
        for m in rag_examples:
            args_compact = json.dumps(m.args, ensure_ascii=False, separators=(",", ":"))
            lines.append(f'- "{m.content}" → {m.tool}({args_compact})')
        system_prompt = system_prompt + "\n" + "\n".join(lines)

    # Bounded conversation memory: when the API layer threads prior turns,
    # fold the last few (question → tool(args)) into a dedicated system
    # message so the model can resolve follow-ups ("the next 50", "that
    # route") without re-stating context. Capped to the most recent three
    # turns and truncated per-question to keep the prompt small and the
    # behaviour deterministic. ``history=[]``/``None`` leaves the message
    # list byte-identical to the no-memory path.
    history_block = None
    if history:
        import json as _json

        header = "== Conversation so far ==" if locale == "en" else "== これまでの会話 =="
        lines = [header]
        for i, turn in enumerate(history[-3:], 1):
            q = str(turn.get("question", ""))[:200]
            tool = turn.get("tool")
            args = turn.get("args") or {}
            if tool:
                args_c = _json.dumps(args, ensure_ascii=False, separators=(",", ":"))
                lines.append(f"{i}. {q} → {tool}({args_c})")
            else:
                lines.append(f"{i}. {q}")
        history_block = "\n".join(lines)

    def _sync():
        # The adapter handles per-provider retries, rate-limit fallback,
        # and logging internally; on total failure (or zero configured
        # providers) it returns None and we surface the standard
        # "service_unreachable" message below.
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "system", "content": locale_addendum},
        ]
        if history_block:
            messages.append({"role": "system", "content": history_block})
        messages.append({"role": "user", "content": user_prelude})
        return client.chat_completions(
            messages=messages,
            tools=TOOLS,
            tool_choice="auto",
            temperature=0.0,
            model_override=model,
        )

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
