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
import os

import asyncpg
import clickhouse_connect
from fastapi import HTTPException

from api.range import RangeCtx
from pipeline.query.intent import IntentSignature, canonicalize, derive_confidence, signature_hash
from pipeline.query.intent_cache import lookup as _cache_lookup
from pipeline.query.intent_cache import lookup_by_question as _cache_lookup_by_question
from pipeline.query.intent_cache import upsert as _cache_upsert
from pipeline.query.llm_client import get_client
from pipeline.query.tools import (
    JSON_MODE_ADDENDUM,
    LOCALE_LANGUAGE_NAME,
    SYSTEM_PROMPT,
    TOOLS,
    ToolResult,
    _summary,
    dispatch,
    render_tool_result,
)

_log = logging.getLogger(__name__)


def _cache_enabled() -> bool:
    """Return True when the intent-cache feature flag is on."""
    return os.environ.get("ASK_INTENT_CACHE_ENABLED", "false").lower() in ("1", "true", "yes")


def _allowed_providers() -> set[str] | None:
    """Providers the primary Ask path may use, from ``ASK_CHAT_ALLOWED_PROVIDERS``.

    This is a hard allowlist, not a soft preference: if set, only these
    providers are tried, and an empty intersection with the configured
    ``CHAT_PROVIDERS`` ladder fails the request rather than falling back
    (see ``LLMClient.chat_completions``'s ``allowed_providers`` handling).
    Unset by default (returns ``None``, i.e. no restriction) so the
    documented historical default — Groq — is unchanged. Some models
    (notably Groq ``llama-3.3-70b``) have been shown to obey instructions
    injected into user text rather than the system prompt (see
    ``pipeline/query/followup.py``, which defaults to Cerebras-only for
    exactly this reason); unlike the follow-up path, restricting the
    primary Ask path by default would change cost/latency/answer-quality
    for the main feature, so operators opt in explicitly here instead.
    """
    raw = os.environ.get("ASK_CHAT_ALLOWED_PROVIDERS", "").strip()
    if not raw:
        return None
    names = {n.strip().lower() for n in raw.split(",") if n.strip()}
    return names or None


_BUILD_SENTINEL = "__build__"


def _parse_build_sentinel(question: str) -> tuple[str, dict] | None:
    """Extract ``(tool, args)`` from a ``__build__ TOOL {json}`` question string.

    The guided build form on the frontend submits the user's structured intent
    as a question with this sentinel prefix so the orchestrator can dispatch it
    **without calling the LLM** — the spec's "zero-LLM determinism path."
    Returns ``None`` if the string doesn't match the format (caller falls
    through to the normal LLM path).
    """
    if not question.startswith(_BUILD_SENTINEL):
        return None
    rest = question[len(_BUILD_SENTINEL) :].lstrip()
    tool, _sep, json_part = rest.partition(" ")
    if not tool:
        return None
    json_part = json_part.strip() or "{}"
    try:
        args = json.loads(json_part)
    except json.JSONDecodeError:
        return None
    if not isinstance(args, dict):
        return None
    return tool, args


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
    ("llm_rate_limited", "ja"): (
        "本日のAIの利用が上限に達しました。路線一覧・遅延ランキング・停留所数などの質問は引き続きご利用いただけます。"
    ),
    ("llm_rate_limited", "en"): (
        "Today's AI usage limit is reached. Questions like route lists, delay rankings, and stop counts still work."
    ),
    ("llm_unconfigured", "ja"): "AIプロバイダーが設定されていません。",
    ("llm_unconfigured", "en"): "No AI provider is configured.",
    ("refusal_fallback", "ja"): "ご質問の内容を理解できませんでした。",
    ("refusal_fallback", "en"): "I couldn't understand your question.",
    # Deliberately does NOT interpolate the exception text (same rationale as
    # service_unavailable below): any exception surfacing here — ClickHouse,
    # asyncpg, or a tool-handler bug — can carry internal details (SQL
    # fragments, relation names, endpoint URLs) that must never reach an
    # unauthenticated client. Full detail is still captured server-side via
    # logger.exception at every call site.
    ("tool_error", "ja"): "ツール {name} の実行中にエラーが発生しました。",
    ("tool_error", "en"): "An error occurred while running tool {name}.",
    # Used when a tool fails due to a backend being unavailable (ClickHouse
    # down at startup, or a mid-query ClickHouse error) rather than a normal
    # tool-logic error. Deliberately does NOT interpolate the exception text —
    # that text can contain internal details (e.g. an asyncpg relation name)
    # that must never reach an unauthenticated client (see api/aggregate_errors.py).
    ("service_unavailable", "ja"): (
        "ツール {name} を実行できませんでした（一時的にサービスに接続できません）。しばらくしてから再度お試しください。"
    ),
    ("service_unavailable", "en"): (
        "Could not run tool {name} right now (temporary service issue). Please retry later."
    ),
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
    ch=None,
) -> dict:
    """Run one round-trip Ask flow.

    ``ch`` is the ClickHouse client threaded through to every internal
    :func:`dispatch` call (Task 8 — handlers reading the live `updates`
    table read it from ClickHouse). Defaults to ``None`` for callers/tests
    that never exercise those specific tool paths.

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
    # Normalize once so leading/trailing whitespace doesn't cause cache misses
    # or visible prompt differences; downstream uses (prompt, cache key, log) all
    # benefit. The frontend keeps its own copy of the user's raw input.
    question = question.strip()
    # Build-mode synthetic questions (sent by the guided form with an ``__build__``
    # prefix) must never be written to the intent cache as last_question, otherwise
    # machine-generated strings surface as chips / autocomplete suggestions.
    _skip_cache_write = question.startswith(_BUILD_SENTINEL)

    # Build-mode short-circuit: when the question is a build-form sentinel,
    # parse (tool, args) directly and dispatch without ever calling the LLM.
    # This is the spec's "zero-LLM determinism path" — confidence=1.0 because
    # the user constructed the query directly. The cache flag is irrelevant
    # here: the sentinel is frontend-controlled and the path must always be
    # deterministic regardless of cache configuration.
    if _skip_cache_write:
        parsed = _parse_build_sentinel(question)
        if parsed is not None:
            build_tool, build_args = parsed
            ctx_dict = {"from_date": ctx.from_date, "to_date": ctx.to_date}
            try:
                can_args = canonicalize(build_tool, build_args, ctx_dict)
            except ValueError:
                # Unknown tool — fall back to dispatching with raw args so the
                # tool handler can return its own "unknown" error string.
                can_args = dict(build_args)
            sig_hash = signature_hash(build_tool, can_args)
            try:
                result = await dispatch(build_tool, can_args, ctx, conn, agency_id, locale=locale, ch=ch)
            except HTTPException as exc:
                if exc.status_code != 503:
                    raise
                _log.warning("Build-mode dispatch for %s unavailable: %s", build_tool, exc.detail)
                return {
                    "answer": _chat_str("service_unavailable", locale, name=build_tool),
                    "tool_call": {"name": build_tool, "arguments": can_args},
                    "result": None,
                    "success": False,
                    "signature_hash": sig_hash,
                    "confidence": 1.0,
                    "canonical_args": can_args,
                    "cache_outcome": "bypass",
                }
            except clickhouse_connect.driver.exceptions.Error:
                _log.exception("Build-mode dispatch for %s: ClickHouse query error", build_tool)
                return {
                    "answer": _chat_str("service_unavailable", locale, name=build_tool),
                    "tool_call": {"name": build_tool, "arguments": can_args},
                    "result": None,
                    "success": False,
                    "signature_hash": sig_hash,
                    "confidence": 1.0,
                    "canonical_args": can_args,
                    "cache_outcome": "bypass",
                }
            except asyncpg.exceptions.UndefinedTableError:
                # An agg_* table missing (migration/analyze behind) must propagate
                # to FastAPI's registered aggregate_not_ready_handler so the
                # frontend gets the machine-readable {"code": "aggregate_not_ready"}
                # 503 it reacts to — not a generic 200 tool_error that masks it
                # (mirrors api/routers/ask.py's Fix-8f convention).
                raise
            except Exception:
                _log.exception("Build-mode dispatch failed for %s", build_tool)
                return {
                    "answer": _chat_str("tool_error", locale, name=build_tool),
                    "tool_call": {"name": build_tool, "arguments": can_args},
                    "result": None,
                    "success": False,
                    "signature_hash": sig_hash,
                    "confidence": 1.0,
                    "canonical_args": can_args,
                    "cache_outcome": "bypass",
                }
            return {
                "answer": render_tool_result(result, locale=locale),
                "tool_call": {"name": build_tool, "arguments": can_args},
                "result": _result_to_dict(result),
                "success": result.kind != "empty",
                "signature_hash": sig_hash,
                "confidence": 1.0,
                "canonical_args": can_args,
                "cache_outcome": "bypass",
            }

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
        header = "== Conversation so far ==" if locale == "en" else "== これまでの会話 =="
        lines = [header]
        for i, turn in enumerate(history[-3:], 1):
            q = str(turn.get("question", ""))[:200]
            tool = turn.get("tool")
            args = turn.get("args") or {}
            if tool:
                # Cap the serialized args: a client could send a huge nested
                # args blob to bloat the prompt / attempt weak prompt injection.
                args_c = json.dumps(args, ensure_ascii=False, separators=(",", ":"))[:200]
                lines.append(f"{i}. {q} → {tool}({args_c})")
            else:
                lines.append(f"{i}. {q}")
        history_block = "\n".join(lines)

    use_cache = _cache_enabled()
    if use_cache:
        # Only the JSON-mode (intent-cache) request should see this format —
        # see JSON_MODE_ADDENDUM's own docstring for why leaking it into the
        # native tool_calls request measurably broke tool-calling for some
        # tools.
        system_prompt = system_prompt + "\n" + JSON_MODE_ADDENDUM

    def _sync():
        """Blocking LLM call executed via ``asyncio.to_thread``.

        Returns ``(message, error_kind)`` where ``message`` is the provider
        response on success and ``None`` on total failure; ``error_kind``
        is ``None`` on success and a short string (``"rate_limit"``,
        ``"connection"``, etc.) on failure.  Both values come directly from
        :meth:`~pipeline.query.llm_client.LLMClient.chat_completions` — no
        shared mutable state is read after the call returns.
        """
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "system", "content": locale_addendum},
        ]
        if history_block:
            messages.append({"role": "system", "content": history_block})
        messages.append({"role": "user", "content": user_prelude})
        if use_cache:
            # JSON-mode emits the signature in message.content directly.
            # Don't send tools+tool_choice with response_format=json_object —
            # OpenAI rejects that combo (400) and providers behave inconsistently.
            return client.chat_completions(
                messages=messages,
                temperature=0.0,
                model_override=model,
                response_format={"type": "json_object"},
                allowed_providers=_allowed_providers(),
            )
        return client.chat_completions(
            messages=messages,
            tools=TOOLS,
            tool_choice="auto",
            temperature=0.0,
            model_override=model,
            allowed_providers=_allowed_providers(),
        )

    # -----------------------------------------------------------------------
    # When cache is enabled, attempt a cache lookup BEFORE calling the LLM.
    # Stage 1: exact question-text pre-lookup — lets us skip the LLM entirely
    # when the same question has been seen before.
    # Stage 2: if question is new, call LLM → compute sig_hash → sig-hash
    # lookup (covers paraphrases that map to the same canonical intent).
    # -----------------------------------------------------------------------
    if use_cache:
        # Stage 1: pre-LLM exact question-text lookup.
        pre_row = await _cache_lookup_by_question(conn, question, agency_id)
        if pre_row is not None:
            # Exact same question seen before — skip LLM entirely.
            _log.debug("Intent cache pre-hit for question %r (sig=%s)", question[:60], pre_row["signature_hash"])
            name = pre_row["tool"]
            args = pre_row["args"] if isinstance(pre_row["args"], dict) else {}
            sig_hash_pre = pre_row["signature_hash"]
            _pre_sig = IntentSignature(tool=name, args=args, confidence=float(pre_row.get("confidence") or 0.0))
            if not _skip_cache_write:
                await _cache_upsert(conn, sig_hash_pre, _pre_sig, args, agency_id, question=question)
            nn_dist_pre = _nn_distance_for_tool(rag_examples or [], name)
            final_conf_pre = derive_confidence(nn_dist_pre, float(pre_row.get("confidence") or 0.0))
            try:
                result_pre: ToolResult = await dispatch(name, args, ctx, conn, agency_id, locale=locale, ch=ch)
            except HTTPException as exc:
                if exc.status_code != 503:
                    raise
                _log.warning("Tool %s unavailable (cache pre-hit): %s", name, exc.detail)
                return {
                    "answer": _chat_str("service_unavailable", locale, name=name),
                    "tool_call": {"name": name, "arguments": args},
                    "result": None,
                    "success": False,
                    "signature_hash": sig_hash_pre,
                    "confidence": final_conf_pre,
                    "canonical_args": args,
                    "cache_outcome": "hit",
                }
            except clickhouse_connect.driver.exceptions.Error:
                _log.exception("Tool %s failed (cache pre-hit): ClickHouse query error", name)
                return {
                    "answer": _chat_str("service_unavailable", locale, name=name),
                    "tool_call": {"name": name, "arguments": args},
                    "result": None,
                    "success": False,
                    "signature_hash": sig_hash_pre,
                    "confidence": final_conf_pre,
                    "canonical_args": args,
                    "cache_outcome": "hit",
                }
            except asyncpg.exceptions.UndefinedTableError:
                # Missing agg_* table must propagate to aggregate_not_ready_handler,
                # not be swallowed into a generic tool_error (see Site 1 comment).
                raise
            except Exception:
                _log.exception("Tool %s failed (cache pre-hit)", name)
                return {
                    "answer": _chat_str("tool_error", locale, name=name),
                    "tool_call": {"name": name, "arguments": args},
                    "result": None,
                    "success": False,
                    "signature_hash": sig_hash_pre,
                    "confidence": final_conf_pre,
                    "canonical_args": args,
                    "cache_outcome": "hit",
                }
            return {
                "answer": render_tool_result(result_pre, locale=locale),
                "tool_call": {"name": name, "arguments": args},
                "result": _result_to_dict(result_pre),
                "success": result_pre.kind != "empty",
                "signature_hash": sig_hash_pre,
                "confidence": final_conf_pre,
                "canonical_args": args,
                "cache_outcome": "hit",
            }

        # Stage 2: question is new — call LLM to get the intent signature.
        msg, error_kind = await asyncio.to_thread(_sync)
        if msg is None:
            key = {
                "rate_limit": "llm_rate_limited",
                "no_providers": "llm_unconfigured",
            }.get(error_kind or "", "service_unreachable")
            return {
                "answer": _chat_str(key, locale),
                "tool_call": None,
                "result": None,
                "success": False,
                "signature_hash": None,
                "confidence": None,
                "canonical_args": None,
                "cache_outcome": None,
            }

        # Try to parse JSON signature from content.
        sig: IntentSignature | None = None
        content = (getattr(msg, "content", None) or "").strip()
        if content:
            try:
                payload = json.loads(content)
                if isinstance(payload, dict) and "tool" in payload:
                    sig = IntentSignature(
                        tool=str(payload["tool"]),
                        args=payload.get("args") or {},
                        confidence=float(payload.get("confidence") or 0.0),
                        rationale=str(payload.get("rationale") or ""),
                    )
            except (json.JSONDecodeError, TypeError, ValueError) as exc:
                _log.warning("JSON-mode parse failed; falling back to tool_calls path: %s", exc)

        if sig is None:
            # Graceful degradation: malformed JSON — fall through to Phase-①
            # tool_calls path below.
            _log.info("Cache path: falling back to Phase-① tool_calls dispatch")
            tool_calls = getattr(msg, "tool_calls", None)
            if not tool_calls:
                # Do NOT surface raw LLM content as the answer in JSON-mode.
                # The LLM is constrained to emit JSON, so any non-tool-call
                # content (e.g. it echoed ``{"type":"json_object"}`` on an
                # adversarial prompt) is structurally invalid output. Show the
                # generic refusal instead — never leak raw model text.
                return {
                    "answer": _chat_str("refusal_fallback", locale),
                    "tool_call": None,
                    "result": None,
                    "success": False,
                    "signature_hash": None,
                    "confidence": None,
                    "canonical_args": None,
                    "cache_outcome": None,
                }
            call = tool_calls[0]
            name = call.function.name
            try:
                args = json.loads(call.function.arguments or "{}")
            except (json.JSONDecodeError, TypeError):
                args = {}
            if not isinstance(args, dict):
                args = {}
            try:
                result = await dispatch(name, args, ctx, conn, agency_id, locale=locale, ch=ch)
            except HTTPException as exc:
                if exc.status_code != 503:
                    raise
                _log.warning("Tool %s unavailable: %s", name, exc.detail)
                return {
                    "answer": _chat_str("service_unavailable", locale, name=name),
                    "tool_call": {"name": name, "arguments": args},
                    "result": None,
                    "success": False,
                    "signature_hash": None,
                    "confidence": None,
                    "canonical_args": None,
                    "cache_outcome": None,
                }
            except clickhouse_connect.driver.exceptions.Error:
                _log.exception("Tool %s failed: ClickHouse query error", name)
                return {
                    "answer": _chat_str("service_unavailable", locale, name=name),
                    "tool_call": {"name": name, "arguments": args},
                    "result": None,
                    "success": False,
                    "signature_hash": None,
                    "confidence": None,
                    "canonical_args": None,
                    "cache_outcome": None,
                }
            except asyncpg.exceptions.UndefinedTableError:
                # Missing agg_* table must propagate to aggregate_not_ready_handler,
                # not be swallowed into a generic tool_error (see Site 1 comment).
                raise
            except Exception:
                _log.exception("Tool %s failed", name)
                return {
                    "answer": _chat_str("tool_error", locale, name=name),
                    "tool_call": {"name": name, "arguments": args},
                    "result": None,
                    "success": False,
                    "signature_hash": None,
                    "confidence": None,
                    "canonical_args": None,
                    "cache_outcome": None,
                }
            return {
                "answer": render_tool_result(result, locale=locale),
                "tool_call": {"name": name, "arguments": args},
                "result": _result_to_dict(result),
                "success": result.kind != "empty",
                "signature_hash": None,
                "confidence": None,
                "canonical_args": None,
                "cache_outcome": None,
            }

        # We have a valid IntentSignature — canonicalize and compute hash.
        ctx_dict = {"from_date": ctx.from_date, "to_date": ctx.to_date}
        try:
            can_args = canonicalize(sig.tool, sig.args, ctx_dict)
        except ValueError:
            # Unknown tool from LLM — degrade gracefully.
            can_args = dict(sig.args)
        sig_hash = signature_hash(sig.tool, can_args)

        # Cache lookup: if found, use the cached (tool, args) and skip re-dispatch.
        cache_row = await _cache_lookup(conn, sig_hash, agency_id)
        if cache_row is not None:
            name = cache_row["tool"]
            args = cache_row["args"] if isinstance(cache_row["args"], dict) else {}
            cache_outcome = "hit"
        else:
            name = sig.tool
            args = can_args
            cache_outcome = "miss"

        # Upsert regardless of hit/miss (bumps hit_count on hit).
        # Skip writes for build-mode synthetic questions so machine-generated
        # strings never appear as last_question in the cache.
        # Also skip when the LLM hallucinated a tool name we don't dispatch —
        # otherwise an out-of-scope refusal (sig.tool='none', etc.) gets
        # cached and every future similar question collapses to the same
        # garbage hash, locking out the LLM permanently.
        from pipeline.query.intent import _TOOL_DEFAULTS as _KNOWN_TOOLS

        _known_tool = sig.tool in _KNOWN_TOOLS
        if not _skip_cache_write and _known_tool:
            await _cache_upsert(conn, sig_hash, sig, can_args, agency_id, question=question)

        # Compute final confidence blending NN distance + LLM self-report.
        nn_dist = _nn_distance_for_tool(rag_examples or [], name)
        final_conf = derive_confidence(nn_dist, sig.confidence)

        try:
            result = await dispatch(name, args, ctx, conn, agency_id, locale=locale, ch=ch)
        except HTTPException as exc:
            if exc.status_code != 503:
                raise
            _log.warning("Tool %s unavailable: %s", name, exc.detail)
            return {
                "answer": _chat_str("service_unavailable", locale, name=name),
                "tool_call": {"name": name, "arguments": args},
                "result": None,
                "success": False,
                "signature_hash": sig_hash,
                "confidence": final_conf,
                "canonical_args": can_args,
                "cache_outcome": cache_outcome,
            }
        except clickhouse_connect.driver.exceptions.Error:
            _log.exception("Tool %s failed: ClickHouse query error", name)
            return {
                "answer": _chat_str("service_unavailable", locale, name=name),
                "tool_call": {"name": name, "arguments": args},
                "result": None,
                "success": False,
                "signature_hash": sig_hash,
                "confidence": final_conf,
                "canonical_args": can_args,
                "cache_outcome": cache_outcome,
            }
        except asyncpg.exceptions.UndefinedTableError:
            # Missing agg_* table must propagate to aggregate_not_ready_handler,
            # not be swallowed into a generic tool_error (see Site 1 comment).
            raise
        except Exception:
            _log.exception("Tool %s failed", name)
            return {
                "answer": _chat_str("tool_error", locale, name=name),
                "tool_call": {"name": name, "arguments": args},
                "result": None,
                "success": False,
                "signature_hash": sig_hash,
                "confidence": final_conf,
                "canonical_args": can_args,
                "cache_outcome": cache_outcome,
            }

        return {
            "answer": render_tool_result(result, locale=locale),
            "tool_call": {"name": name, "arguments": args},
            "result": _result_to_dict(result),
            "success": result.kind != "empty",
            "signature_hash": sig_hash,
            "confidence": final_conf,
            "canonical_args": can_args,
            "cache_outcome": cache_outcome,
        }

    # -----------------------------------------------------------------------
    # FLAG-OFF path: byte-identical to Phase ①. No cache reads or writes.
    # -----------------------------------------------------------------------
    msg, error_kind = await asyncio.to_thread(_sync)
    if msg is None:
        # The LLM ladder is exhausted — a hard failure, not a deliberate
        # decline. success=False so analytics don't count it.
        # Route by failure kind: quota exhaustion steers the user toward
        # question types Stages 1-2 answer without any LLM; everything else
        # falls back to the generic retry message.
        key = {
            "rate_limit": "llm_rate_limited",
            "no_providers": "llm_unconfigured",
        }.get(error_kind or "", "service_unreachable")
        return {
            "answer": _chat_str(key, locale),
            "tool_call": None,
            "result": None,
            "success": False,
        }

    tool_calls = getattr(msg, "tool_calls", None)
    if not tool_calls:
        # Out-of-scope path: model returned plain text. A non-empty body is a
        # deliberate, helpful refusal/suggestion (the system worked → True).
        # An empty body falls back to the generic "couldn't understand"
        # string, which is a genuine failure to parse the question → False.
        body = (msg.content or "").strip()
        if body:
            return {"answer": body, "tool_call": None, "result": None, "success": True}
        return {
            "answer": _chat_str("refusal_fallback", locale),
            "tool_call": None,
            "result": None,
            "success": False,
        }

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
        result = await dispatch(name, args, ctx, conn, agency_id, locale=locale, ch=ch)
    except HTTPException as exc:
        if exc.status_code != 503:
            raise
        # A ClickHouse-unavailable stand-in raise, not a deliberate decline.
        _log.warning("Tool %s unavailable: %s", name, exc.detail)
        return {
            "answer": _chat_str("service_unavailable", locale, name=name),
            "tool_call": {"name": name, "arguments": args},
            "result": None,
            "success": False,
        }
    except clickhouse_connect.driver.exceptions.Error:
        _log.exception("Tool %s failed: ClickHouse query error", name)
        return {
            "answer": _chat_str("service_unavailable", locale, name=name),
            "tool_call": {"name": name, "arguments": args},
            "result": None,
            "success": False,
        }
    except asyncpg.exceptions.UndefinedTableError:
        # Missing agg_* table must propagate to aggregate_not_ready_handler,
        # not be swallowed into a generic tool_error (see Site 1 comment).
        raise
    except Exception:
        _log.exception("Tool %s failed", name)
        # A tool blowing up is a hard failure, not a deliberate decline.
        return {
            "answer": _chat_str("tool_error", locale, name=name),
            "tool_call": {"name": name, "arguments": args},
            "result": None,
            "success": False,
        }

    return {
        "answer": render_tool_result(result, locale=locale),
        "tool_call": {"name": name, "arguments": args},
        "result": _result_to_dict(result),
        # Mirror the dispatch-path rule: an empty/no-data result is not success.
        "success": result.kind != "empty",
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


def _nn_distance_for_tool(rag_examples: list, tool: str) -> float | None:
    """Return the smallest cosine distance among RAG examples whose tool matches.

    Each element of ``rag_examples`` is expected to have ``.tool`` and
    ``.distance`` attributes (a :class:`~pipeline.query.rag_index.Match`
    enriched by :func:`~pipeline.query.router._enrich`).

    Returns ``None`` when ``rag_examples`` is empty or no example maps to
    ``tool``. Used by Stage 3 to derive a confidence score without calling
    the LLM a second time — the NN distance is already computed upstream.
    """
    distances = [m.distance for m in rag_examples if getattr(m, "tool", None) == tool]
    return min(distances) if distances else None


# Re-export the locale lookup helper so tests / shared code can use the
# same fallback semantics without reaching into the tools module's
# private namespace.
__all__ = ["_nn_distance_for_tool", "_summary", "chat_with_tools"]
