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

from api.middleware.ratelimit import AnonAskQuotaExceeded, AnonQuotaContext, check_and_consume_anon_quota
from api.range import RangeCtx
from pipeline.query.hallucination_guard import verify_numeric_claims
from pipeline.query.intent import IntentSignature, canonicalize, derive_confidence, signature_hash
from pipeline.query.intent_cache import lookup as _cache_lookup
from pipeline.query.intent_cache import lookup_by_question as _cache_lookup_by_question
from pipeline.query.intent_cache import upsert as _cache_upsert
from pipeline.query.llm_client import get_client
from pipeline.query.tools import (
    JSON_MODE_ADDENDUM,
    JSON_MODE_FORCE_TOOL_ADDENDUM,
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


def _consume_anon_quota_or_raise(anon_quota: AnonQuotaContext | None) -> None:
    """Consume one unit of the anon LLM-call quota, or raise if exhausted.

    Shared by both real LLM-invocation sites in :func:`chat_with_tools` so
    they can't drift apart from each other.
    """
    if anon_quota is not None and not check_and_consume_anon_quota(anon_quota.session_key, anon_quota.ip_key):
        raise AnonAskQuotaExceeded()


def _numeric_guard(answer: str | None, grounding: dict, locale: str) -> tuple[str | None, bool]:
    """Replace ``answer`` with the localized fallback if it makes a numeric
    claim not traceable to ``grounding``.

    Only ever called at a site where ``answer`` is LLM-authored free text —
    ``_dispatch_and_respond``'s ``render_tool_result`` output is already
    grounded by construction (a formatted SQL aggregate) and is never routed
    through this helper. ``grounding={}`` means a turn with no dispatched
    data at all (e.g. an out-of-scope refusal). Nothing can be traced there, so
    only a number carrying a metric unit counts as a claim — the bare digits
    that path legitimately contains are route codes and periods the system
    prompt asks the model to name. See
    :func:`pipeline.query.hallucination_guard.verify_numeric_claims`.
    """
    if not answer:
        return answer, False
    if verify_numeric_claims(answer, grounding):
        return answer, False
    return _summary("numeric_guard_fallback", lang=locale), True


async def _dispatch_and_respond(
    name: str,
    args: dict,
    ctx: RangeCtx,
    conn,
    agency_id: int,
    locale: str,
    ch,
    *,
    extra: dict | None = None,
    verb_suffix: str = "",
) -> dict:
    """Call :func:`dispatch` and shape the response dict, handling the
    failure modes shared by every non-build-mode call site in this module
    identically: an HTTPException(503) or a mid-query ClickHouse error both
    degrade to the ``service_unavailable`` string (never the raw exception
    text, which can carry SQL fragments or endpoint URLs — see
    ``api/aggregate_errors.py``); a non-503 HTTPException or a missing
    ``agg_*`` table (``UndefinedTableError``) propagates so a caller's
    ``aggregate_not_ready_handler`` can turn the latter into the
    machine-readable 503 the frontend reacts to; anything else degrades to
    the generic ``tool_error`` string. All failures still log the full
    detail server-side via ``logger.exception``.

    ``extra`` merges additional response keys (``signature_hash``,
    ``confidence``, ``canonical_args``, ``cache_outcome``) used by the
    intent-cache call sites; omitted by the flag-off path, which returns
    only the base four keys. ``verb_suffix`` is appended to the log verb
    (e.g. ``" (cache pre-hit)"``) so call sites stay distinguishable in logs.
    """
    extra = extra or {}
    try:
        result = await dispatch(name, args, ctx, conn, agency_id, locale=locale, ch=ch)
    except HTTPException as exc:
        if exc.status_code != 503:
            raise
        _log.warning("Tool %s unavailable%s: %s", name, verb_suffix, exc.detail)
        return {
            "answer": _chat_str("service_unavailable", locale, name=name),
            "tool_call": {"name": name, "arguments": args},
            "result": None,
            "success": False,
            "numeric_guard_triggered": None,
            **extra,
        }
    except clickhouse_connect.driver.exceptions.Error:
        _log.exception("Tool %s failed%s: ClickHouse query error", name, verb_suffix)
        return {
            "answer": _chat_str("service_unavailable", locale, name=name),
            "tool_call": {"name": name, "arguments": args},
            "result": None,
            "success": False,
            "numeric_guard_triggered": None,
            **extra,
        }
    except asyncpg.exceptions.UndefinedTableError:
        raise
    except Exception:
        _log.exception("Tool %s failed%s", name, verb_suffix)
        return {
            "answer": _chat_str("tool_error", locale, name=name),
            "tool_call": {"name": name, "arguments": args},
            "result": None,
            "success": False,
            "numeric_guard_triggered": None,
            **extra,
        }
    # render_tool_result formats `result` (a dispatched ToolResult) deterministically
    # from grounded data — it never contains LLM-authored prose, so it is not
    # routed through _numeric_guard (see that helper's docstring).
    return {
        "answer": render_tool_result(result, locale=locale),
        "tool_call": {"name": name, "arguments": args},
        "result": _result_to_dict(result),
        "success": result.kind != "empty",
        "numeric_guard_triggered": None,
        **extra,
    }


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
    force_tool_call: bool = False,
    anon_quota: AnonQuotaContext | None = None,
    panel_ctx: dict | None = None,
) -> dict:
    """Run one round-trip Ask flow.

    ``ch`` is the ClickHouse client threaded through to every internal
    :func:`dispatch` call (Task 8 — handlers reading the live `updates`
    table read it from ClickHouse). Defaults to ``None`` for callers/tests
    that never exercise those specific tool paths.

    ``force_tool_call`` is set by the API layer (``api/routers/ask.py``)
    exactly when ``router.is_follow_up(question)`` matched AND the *prior*
    turn actually carried a tool call — i.e. bare continuation phrasing
    ("次の50件", "もっと", "next") that only makes sense as a re-invocation of
    that previous tool with adjusted args. (``is_follow_up()``'s regex also
    matches ordinary continuation phrasing that can legitimately follow a
    free-text answer, e.g. an out-of-scope refusal — the API layer excludes
    that case so a forced tool call is never demanded when there's nothing
    to continue.) For the narrow class this does fire for, a free-text
    reply is never a correct answer, but ``tool_choice="auto"`` still lets
    the model pick one — observed live as a turn-2 pagination follow-up
    ("次の50件" after a stops listing) coming back with ``tool_call: None``
    instead of ``describe_data(kind=stops, offset=50)``, even though the
    system prompt already documents that exact rewrite (see rule 7 in
    ``SYSTEM_PROMPT``). Forcing ``tool_choice="required"`` for this one
    request removes the model's option to decline, closing that failure
    mode without touching the router's designed LLM-routing for follow-ups
    (see ``tests/api/test_api_ask.py::test_follow_up_reroutes_to_llm_with_history``)
    or affecting any other question shape, which keeps ``tool_choice="auto"``.
    Under ``ASK_INTENT_CACHE_ENABLED``, the JSON-mode request can't take a
    ``tool_choice`` at all (see the ``use_cache`` branch below), so
    ``force_tool_call`` instead appends ``JSON_MODE_FORCE_TOOL_ADDENDUM`` —
    a prompt-level instruction rather than an API-level guarantee. Also under
    that flag, ``force_tool_call`` additionally skips the Stage 1 exact-text
    cache pre-hit (see the ``use_cache`` branch below): that lookup is keyed
    on literal question text only, with no notion of ``history``, so serving
    it for a continuation phrase would return whichever answer was last
    cached for that exact text — ignoring this conversation's actual prior
    turn — instead of the history-aware answer this parameter exists to get.

    ``anon_quota``, when set by the API layer for an unauthenticated caller,
    is checked and consumed immediately around each of this function's two
    actual LLM-invocation sites below (never at the build-mode short-circuit
    or an intent-cache hit, both of which skip the LLM entirely, and never
    when ``None`` — i.e. a logged-in caller). Exhaustion raises
    :class:`~api.middleware.ratelimit.AnonAskQuotaExceeded`, which this
    function does not catch — it propagates out to the API layer the same
    way an ``asyncpg.exceptions.UndefinedTableError`` does, so a registered
    FastAPI exception handler can turn it into a machine-readable response.

    ``panel_ctx``, when supplied by the Copilot side panel, carries the
    frontend's active-tab hint (e.g. ``{"tab": "overview"}``). The API layer
    (``api/routers/ask.py``'s ``PanelCtx`` model) restricts ``tab`` to a
    known enum of tab names before it ever reaches this function, so this
    dict-typed parameter is safe to interpolate into the system prompt
    without its own length/type check. It is appended to the system prompt
    as a one-line grounding hint only — it never reaches the rules/embedding
    routing stage (resolved before calling this function) or the
    tool-dispatch args.

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
                    "numeric_guard_triggered": None,
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
                    "numeric_guard_triggered": None,
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
                    "numeric_guard_triggered": None,
                    "signature_hash": sig_hash,
                    "confidence": 1.0,
                    "canonical_args": can_args,
                    "cache_outcome": "bypass",
                }
            # render_tool_result formats a dispatched ToolResult deterministically —
            # never LLM-authored prose — so it is not routed through _numeric_guard.
            return {
                "answer": render_tool_result(result, locale=locale),
                "tool_call": {"name": build_tool, "arguments": can_args},
                "result": _result_to_dict(result),
                "success": result.kind != "empty",
                "numeric_guard_triggered": None,
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

    # Copilot side-panel hint: a one-line grounding addendum naming the
    # active tab, appended (never replacing the block above) so the
    # rules/embedding routing stage — already resolved by the caller before
    # this function runs — and tool-dispatch args stay untouched.
    if panel_ctx and panel_ctx.get("tab"):
        system_prompt += f"\nThe user is currently viewing the {panel_ctx['tab']} tab."

    # Bounded conversation memory: when the API layer threads prior turns,
    # fold the last few (question → tool(args)) into a dedicated system
    # message so the model can resolve follow-ups ("the next 50", "that
    # route") without re-stating context. Capped to the most recent three
    # turns and truncated per-question to keep the prompt small and the
    # behaviour deterministic. ``history=[]``/``None`` leaves the message
    # list byte-identical to the no-memory path.
    #
    # ``history`` is attached whenever the API layer has prior turns to
    # offer, regardless of whether ``router.is_follow_up()`` matched the
    # current question — that regex only flags explicit continuation
    # phrasing, but generic anaphora ("その路線は？") also needs the prior
    # turn and doesn't match it. That means this block is just as often
    # attached ahead of a genuinely NEW, unrelated question that merely
    # failed to get a confident Stage 1/2 match, which risks the model
    # answering from the attached (unrelated) history text instead of
    # recognizing that the current question needs a fresh tool call. The
    # trailing guard line below makes the scope of "use history" explicit
    # so the model doesn't default to treating unrelated prior data as the
    # answer key for an unrelated question.
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
        guard = (
            (
                "上記は、現在の質問がそれを明示的に参照している場合"
                "(「その路線は？」「この結果の続き」など)にのみ解釈の助けとして使う。"
                "現在の質問が上記と無関係な新しい話題であれば、上記のデータで答えようとせず、"
                "現在の質問だけに対して適切なツールを呼び出すこと。"
            )
            if locale != "en"
            else (
                "Only use the above to help interpret the current question when it "
                'explicitly references it (e.g. "that route", "continue that '
                'result"). If the current question is a new, unrelated topic, do '
                "not try to answer it from the data above — call the appropriate "
                "tool for the current question on its own."
            )
        )
        lines.append(guard)
        history_block = "\n".join(lines)

    use_cache = _cache_enabled()
    if use_cache:
        # Only the JSON-mode (intent-cache) request should see this format —
        # see JSON_MODE_ADDENDUM's own docstring for why leaking it into the
        # native tool_calls request measurably broke tool-calling for some
        # tools.
        system_prompt = system_prompt + "\n" + JSON_MODE_ADDENDUM
        if force_tool_call:
            # response_format=json_object can't be combined with tool_choice
            # (see the comment below), so under the intent-cache flag
            # force_tool_call has no API-level lever to pull — this prompt
            # instruction is the JSON-mode equivalent of tool_choice="required".
            system_prompt = system_prompt + "\n" + JSON_MODE_FORCE_TOOL_ADDENDUM

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
            tool_choice="required" if force_tool_call else "auto",
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
        #
        # Keyed on literal question text + agency only — it has no notion of
        # ``history``, so a generic continuation phrase ("次の50件") would
        # return whichever (tool, args) was last cached for that exact text
        # by *any* user in the agency, ignoring this conversation's actual
        # prior turn. When force_tool_call is set (a recognized continuation
        # that must be resolved against this history), skip the pre-hit and
        # fall through to Stage 2 below so the LLM call actually sees
        # history_block instead of returning a stale, history-blind answer.
        pre_row = None if force_tool_call else await _cache_lookup_by_question(conn, question, agency_id)
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
            return await _dispatch_and_respond(
                name,
                args,
                ctx,
                conn,
                agency_id,
                locale,
                ch,
                extra={
                    "signature_hash": sig_hash_pre,
                    "confidence": final_conf_pre,
                    "canonical_args": args,
                    "cache_outcome": "hit",
                },
                verb_suffix=" (cache pre-hit)",
            )

        # Stage 2: question is new — call LLM to get the intent signature.
        # The anon quota gates the actual LLM call, not the cache pre-hit
        # above (which never reaches here) — see this function's docstring.
        _consume_anon_quota_or_raise(anon_quota)
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
                "numeric_guard_triggered": None,
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
                    "numeric_guard_triggered": None,
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
            return await _dispatch_and_respond(
                name,
                args,
                ctx,
                conn,
                agency_id,
                locale,
                ch,
                extra={
                    "signature_hash": None,
                    "confidence": None,
                    "canonical_args": None,
                    "cache_outcome": None,
                },
            )

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

        return await _dispatch_and_respond(
            name,
            args,
            ctx,
            conn,
            agency_id,
            locale,
            ch,
            extra={
                "signature_hash": sig_hash,
                "confidence": final_conf,
                "canonical_args": can_args,
                "cache_outcome": cache_outcome,
            },
        )

    # -----------------------------------------------------------------------
    # FLAG-OFF path: same tool-calling flow as Phase ① (no cache reads/
    # writes), gated by the anon-quota check above when one is set.
    # -----------------------------------------------------------------------
    _consume_anon_quota_or_raise(anon_quota)
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
            "numeric_guard_triggered": None,
        }

    tool_calls = getattr(msg, "tool_calls", None)
    if not tool_calls:
        # Out-of-scope path: model returned plain text. A non-empty body is a
        # deliberate, helpful refusal/suggestion (the system worked → True).
        # An empty body falls back to the generic "couldn't understand"
        # string, which is a genuine failure to parse the question → False.
        # This is the one LLM-authored-free-text site in this function — no
        # tool was dispatched, so there is no data to trace a number back to.
        # _numeric_guard is called with grounding={}, which rejects a
        # unit-bearing statistic the model invented here while letting through
        # the route codes and periods a helpful refusal is supposed to name.
        body = (msg.content or "").strip()
        if body:
            guarded_body, triggered = _numeric_guard(body, {}, locale)
            return {
                "answer": guarded_body,
                "tool_call": None,
                "result": None,
                "success": True,
                "numeric_guard_triggered": triggered,
            }
        return {
            "answer": _chat_str("refusal_fallback", locale),
            "tool_call": None,
            "result": None,
            "success": False,
            "numeric_guard_triggered": None,
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

    return await _dispatch_and_respond(name, args, ctx, conn, agency_id, locale, ch)


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
