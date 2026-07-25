"""LLM tool-use surface for the Ask tab (v2).

Replaces the v1 single-intent classifier. The LLM now picks one of six
generic tools, each scoped to the request's :class:`~api.range.RangeCtx`.
Anything outside the tool surface (weather, fares, accidents) prompts the
model to refuse naturally and suggest 2–3 supported alternatives instead
of falling off the cliff with ``unknown: true``.

Public surface:

* :data:`TOOLS` — Groq-style function specs to pass as ``tools=`` on the
  chat-completions call.
* :func:`dispatch` — execute a tool call against Postgres and return a
  :class:`ToolResult`.
* :func:`render_tool_result` — produce a locale-appropriate summary
  string the frontend can show in the assistant bubble.

Localisation
------------
Every user-facing string is keyed on ``(template, locale)`` via
:data:`_LOCALES`. Handlers thread ``locale`` (``"ja"`` or ``"en"``) from
the middleware-driven request state into :func:`_summary`, so the same
data path renders in whichever language the UI is set to. The wire
contract column codes (``route_code``, ``service_type`` …) stay English
because the frontend i18n layer translates display names client-side.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import date, timedelta
from typing import Any, Literal

from api.range import MAX_RANGE_DAYS, RangeCtx, ServiceType, jst_today
from pipeline import perf
from pipeline.query.labels import dow_label
from pipeline.query.results import ToolResult
from pipeline.query.tool_queries import (
    route_compare_service,
    route_dow_breakdown,
    route_info,
)
from pipeline.reports import (
    compute_compare_ranking,
    compute_on_time,
    compute_ranking,
    compute_trend_series,
    compute_worst_5min,
)

TopNMetric = Literal["avg_delay", "on_time_rate", "worst_5min"]


# ---------------------------------------------------------------------------
# Localisation table
# ---------------------------------------------------------------------------


# Keyed by (template_name, locale). Values are ``str.format``-style
# templates so handlers can interpolate route codes, counts, etc. without
# string-concatenation noise. Add a new template here rather than peppering
# inline ``if locale == "en"`` conditionals through the handlers.
_LOCALES: dict[tuple[str, str], str] = {
    ("route_arg_required", "ja"): "route 引数が必要です。",
    ("route_arg_required", "en"): "The route argument is required.",
    ("route_not_registered", "ja"): (
        "'{route}' は登録されている路線コードではありません。/api/{agency_id}/routes で一覧を確認してください。"
    ),
    ("route_not_registered", "en"): (
        "'{route}' is not a registered route code. See /api/{agency_id}/routes for the full list."
    ),
    ("route_no_data", "ja"): (
        "路線{route} の集計データが選択期間 ({from_date}〜{to_date}) にありません。"
        "期間を広げるか、フィルタを解除して試してください。"
    ),
    ("route_no_data", "en"): (
        "No aggregated data for route {route} in the selected window "
        "({from_date} to {to_date}). Try widening the range or clearing filters."
    ),
    ("route_summary", "ja"): "路線{route} の遅延サマリ",
    ("route_summary", "en"): "Delay summary for route {route}",
    ("unknown_metric", "ja"): "未知の metric: {metric}",
    ("unknown_metric", "en"): "Unknown metric: {metric}",
    ("no_data", "ja"): "データがありません。",
    ("no_data", "en"): "No data available.",
    ("ranking_summary", "ja"): "{label}ランキング 上位{count}路線",
    ("ranking_summary", "en"): "{label} ranking, top {count} routes",
    ("ranking_summary_worst", "ja"): "{label}ランキング 下位{count}路線",
    ("ranking_summary_worst", "en"): "{label} ranking, bottom {count} routes",
    ("label_ranking_ontime", "ja"): "定時運行",
    ("label_ranking_ontime", "en"): "On-time",
    ("label_ranking_delay", "ja"): "遅延",
    ("label_ranking_delay", "en"): "Delay",
    ("label_ranking_ontime_rate", "ja"): "定時率",
    ("label_ranking_ontime_rate", "en"): "On-time rate",
    ("label_ranking_late5", "ja"): "5分以上遅延件数",
    ("label_ranking_late5", "en"): "5+ minute delay count",
    ("compare_no_data", "ja"): "比較に必要なデータがありません。",
    ("compare_no_data", "en"): "Not enough data to compare.",
    ("compare_summary_dow", "ja"): "平日 vs 土日祝 遅延比較",
    ("compare_summary_dow", "en"): "Weekday vs weekend/holiday delay comparison",
    ("compare_service_needs_route", "ja"): "dimension=service_type の場合は route が必要です。",
    ("compare_service_needs_route", "en"): "dimension=service_type requires a route argument.",
    ("compare_route_no_data", "ja"): "路線{route} の比較データなし。",
    ("compare_route_no_data", "en"): "No comparison data for route {route}.",
    ("compare_summary_service", "ja"): "路線{route} 種別比較",
    ("compare_summary_service", "en"): "Service-type comparison for route {route}",
    ("unknown_dimension", "ja"): "未知の dimension: {dimension}",
    ("unknown_dimension", "en"): "Unknown dimension: {dimension}",
    ("trend_no_data", "ja"): "期間内に観測データがありません。",
    ("trend_no_data", "en"): "No observations in the selected window.",
    ("trend_summary", "ja"): "日次トレンド ({from_date}〜{to_date}): 平均{avg:.2f}分",
    ("trend_summary", "en"): "Daily trend ({from_date} to {to_date}): mean {avg:.2f} min",
    ("on_time_no_data", "ja"): "定時率を計算できるデータがありません。",
    ("on_time_no_data", "en"): "Not enough data to compute on-time rate.",
    ("on_time_summary", "ja"): "定時率 (遅延 {threshold_min} 分以内) 上位{count}路線",
    ("on_time_summary", "en"): "On-time rate (within {threshold_min} min) — top {count} routes",
    ("route_meta_not_found", "ja"): "路線{route} の路線情報が見つかりません。",
    ("route_meta_not_found", "en"): "No metadata found for route {route}.",
    ("route_meta_summary", "ja"): "路線{route} 路線情報",
    ("route_meta_summary", "en"): "Route info — {route}",
    ("meta_label_name", "ja"): "路線名",
    ("meta_label_name", "en"): "Route name",
    ("meta_label_stops", "ja"): "停留所数",
    ("meta_label_stops", "en"): "Stops",
    ("meta_label_first", "ja"): "始発",
    ("meta_label_first", "en"): "First departure",
    ("meta_label_last", "ja"): "最終",
    ("meta_label_last", "en"): "Last departure",
    ("meta_label_trips", "ja"): "運行便数",
    ("meta_label_trips", "en"): "Daily trips",
    ("meta_stops_value", "ja"): "{n}駅",
    ("meta_stops_value", "en"): "{n}",
    ("meta_trips_value", "ja"): "{n}便",
    ("meta_trips_value", "en"): "{n}",
    ("dash", "ja"): "—",
    ("dash", "en"): "—",
    ("unsupported_tool", "ja"): "未対応のツール: {name}",
    ("unsupported_tool", "en"): "Unsupported tool: {name}",
    ("route_did_you_mean", "ja"): "'{raw}' は見つかりません。もしかして: {candidates}",
    ("route_did_you_mean", "en"): "'{raw}' not found. Did you mean: {candidates}",
    ("did_you_mean_candidate", "ja"): "路線{code}({name})",
    ("did_you_mean_candidate", "en"): "route {code} ({name})",
    # render_tool_result decorations
    ("series_top_offenders", "ja"): " (悪化: {routes})",
    ("series_top_offenders", "en"): " (worst: {routes})",
    ("series_line", "ja"): "{date}: 平均{avg:.2f}分 / {samples}件{top}",
    ("series_line", "en"): "{date}: mean {avg:.2f} min / {samples} samples{top}",
    ("more_rows", "ja"): "…他{n}件",
    ("more_rows", "en"): "…{n} more",
    ("route_prefix", "ja"): "路線{route}",
    ("route_prefix", "en"): "route {route}",
}


def _summary(template: str, lang: str = "ja", **vars: Any) -> str:
    """Resolve a localised template, falling back to JP when missing.

    The fallback keeps the behaviour predictable if an EN string is added
    later but the lookup table is briefly inconsistent — the user sees JP
    text rather than a KeyError 500.
    """
    if lang not in ("ja", "en"):
        lang = "ja"
    tpl = _LOCALES.get((template, lang)) or _LOCALES.get((template, "ja"), template)
    try:
        return tpl.format(**vars) if vars else tpl
    except KeyError:
        return tpl


# ---------------------------------------------------------------------------
# Groq function specs (v2 tool surface)
# ---------------------------------------------------------------------------

_DATE_OVERRIDE_PROPS = {
    "days_back": {
        "type": "integer",
        "minimum": 1,
        "maximum": 365,
        "description": "Override window: from = today - days_back + 1, to = today.",
    },
    "from": {"type": "string", "format": "date", "description": "ISO YYYY-MM-DD start (override)."},
    "to": {"type": "string", "format": "date", "description": "ISO YYYY-MM-DD end (override)."},
}


TOOLS: list[dict] = [
    {
        "type": "function",
        "function": {
            "name": "route_stats",
            "description": (
                "Aggregate delay statistics for ONE specific route over the request "
                "window. Use when the user asks about how a particular bus route is "
                "doing (e.g. '路線5の遅延', '44372はどう?'). Always returns "
                "per-service-type rows."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "route": {"type": "string", "description": "route_code, digits only e.g. '16071'"},
                    **_DATE_OVERRIDE_PROPS,
                },
                "required": ["route"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "top_n",
            "description": (
                "Sorted ranking across all routes. Use for 'worst N', 'best N', "
                "'most 5-minute delays'. The metric param controls which dimension "
                "is ranked: avg_delay (default, longest avg first), on_time_rate "
                "(highest on-time % first), worst_5min (most >5min incidents first)."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "metric": {
                        "type": "string",
                        "enum": ["avg_delay", "on_time_rate", "worst_5min"],
                    },
                    "n": {"type": "integer", "minimum": 3, "maximum": 100},
                    "best_first": {
                        "type": "boolean",
                        "description": (
                            "When true, sort ascending (best first). Defaults to false "
                            "for avg_delay/worst_5min, true for on_time_rate."
                        ),
                    },
                    **_DATE_OVERRIDE_PROPS,
                },
                "required": ["metric"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "compare_segments",
            "description": (
                "Side-by-side delay comparison for one route, splitting on weekday "
                "vs weekend (dimension=dow) or service_type. Use for '平日と土日祝の比較'."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "route": {"type": "string"},
                    "dimension": {"type": "string", "enum": ["dow", "service_type"]},
                    **_DATE_OVERRIDE_PROPS,
                },
                "required": ["dimension"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "time_series",
            "description": (
                "Daily delay series across the window. Use for 'トレンド', '推移', "
                "'最近の傾向'. Returns per-day avg + sample counts + top-3 worst routes."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "route": {"type": "string", "description": "Optional — if set, filter to this route_code."},
                    **_DATE_OVERRIDE_PROPS,
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "on_time_rate",
            "description": (
                "On-time percentage per route. Default threshold is 60 seconds; "
                "set threshold_min=5 to compute '5分以内定時率' instead."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "threshold_min": {"type": "integer", "minimum": 0, "maximum": 30},
                    "n": {"type": "integer", "minimum": 3, "maximum": 100},
                    **_DATE_OVERRIDE_PROPS,
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "route_meta",
            "description": (
                "Static metadata for a route: name, stop count, first/last departure, "
                "trips per day. Use for '路線情報', 'について教えて'."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "route": {"type": "string"},
                },
                "required": ["route"],
            },
        },
    },
]


from pipeline.query.meta_tools import META_HANDLERS, META_TOOLS  # noqa: E402

# Mutate the existing list/dict in place rather than rebinding the names.
# Any module that imported ``TOOLS`` or ``_HANDLERS`` before meta-tools
# wired in (e.g. ``from pipeline.query.tools import TOOLS`` cached at
# import time) keeps seeing the same object — and therefore the merged
# entries — instead of holding a stale reference to the pre-merge list.
TOOLS.extend(META_TOOLS)


SYSTEM_PROMPT = """\
あなたは青森市バスの遅延分析アシスタントです。利用可能なツールを使って質問に答えます。

== 重要なルール ==
1. ツールが質問に合うなら必ずツールを呼び出す。前置きや説明文は不要。
2. **route 引数は実際のシステム route_code(4〜5桁の数字、例: '16071', '22171')を渡す。**
   ユーザーが '路線5' のような短い別名や 'A1'・'中央大橋線' のような日本語名で route を
   指定してきた場合も、そのまま route に渡してよい。dispatch 層の schema_linker が別名を
   解決する。それでも解決できない曖昧な入力(例: '雨天')の場合は、route 引数を埋めて
   ツールを呼ぶのではなく、データ可用性を確かめるなら `describe_data`、
   答えられる質問例を見せるなら `capabilities` を呼ぶ。
3. データの提供範囲外の質問(天気、運賃、事故、車両情報など)はツールを呼ばず、
   利用できるデータを伝え、関連する答えられる質問を 2〜3 件提案する。
4. **期間の上書き**: ユーザーが「直近X日/週/月」「過去N日」「先週」「先月」「昨日」など
   特定の期間を明示した場合、`days_back` (整数日) または `from`/`to` (YYYY-MM-DD) 引数で
   ツールに渡してUIのデフォルト範囲を上書きする。指定がなければ何も渡さない(UIの範囲が使われる)。
   - 「直近2週間の傾向」→ time_series(days_back=14)
   - 「先月の定時率」→ on_time_rate(days_back=30) (シンプルに30日と解釈)
   - 「過去3日の遅延」→ top_n(metric='avg_delay', n=10, days_back=3)
5. 曜日/時間帯フィルタはツール引数で上書きする必要はない(UIで適用済み)。
6. **ツールに合わない質問・データ可用性の質問** → まず `describe_data`
   (データ範囲・路線・停留所など) または `capabilities`(答えられる質問の例) を呼ぶ。
   自然文での拒否は本当にデータ範囲外(天気・運賃・事故など) の場合のみ。
7. **リスト表示の後に「もっと」「次の50件」などと聞かれたら、同じツールを `offset` を
   `limit` 分増やして再呼び出しする（例: 停留所一覧の続き → describe_data(kind=stops, offset=50)）。

== 利用可能なツール ==
- route_stats(route, days_back?, from?, to?): 1 路線の遅延統計
- top_n(metric, n?, best_first?, days_back?, from?, to?): 全路線ランキング
- compare_segments(route?, dimension, days_back?, from?, to?): 平日 vs 土日祝などの比較
- time_series(route?, days_back?, from?, to?): 日次トレンド
- on_time_rate(threshold_min?, n?, days_back?, from?, to?): 定時率ランキング
- route_meta(route): 路線の路線情報
- describe_data(kind, limit?, filter_substring?): データセットそのものの問い合わせ
  (kind ∈ routes/stops/date_range/agencies/sample_counts/overview/metrics)
  例:「どんな路線がある?」→ kind=routes /「いつからのデータ?」→ kind=date_range /
     「サンプル数の多い路線」→ kind=sample_counts /「全体感」→ kind=overview
- capabilities(category?): 答えられる質問例(カテゴリ別)を返す。
  ユーザーの質問が漠然としていたり範囲外の時に使う。
  例:「やばい路線」「いつものやつ」「何ができる?」

== 例 ==
- "今日の遅延ランキング" → top_n(metric='avg_delay', n=10)
- "直近2週間の傾向" → time_series(days_back=14)
- "路線22171の先週の遅延" → route_stats(route='22171', days_back=7)
- "過去3日で5分超が一番多い路線" → top_n(metric='worst_5min', n=10, days_back=3)
- "雨天時の比較" → ツール呼ばず、「天気データはありません。
  代わりに『22171の平日と土日祝の比較』が答えられます」と返す
- "どんな路線がある?" → describe_data(kind='routes')
- "いつからのデータ?" → describe_data(kind='date_range')
- "サンプル数の多い路線は?" → describe_data(kind='sample_counts')
- "データセット全体の概要" → describe_data(kind='overview')
- "何ができる?" / "やばい路線" → capabilities()
- "事故情報を見たい" → capabilities() を呼んで答えられる質問例を返す

== Output format (when asked for JSON) ==
When the request specifies JSON output, return ONLY a JSON object of this shape:
{"tool": "<one of the tools>", "args": {<tool args>}, "confidence": <0..1>, "rationale": "<short reason>"}
No prose, no markdown fences. `confidence` should reflect how sure you are about the tool + args
choice; use lower values when the user's wording is ambiguous.
"""


# Human-readable name of each locale, for the "Reply in ..." system addendum
# (see :mod:`pipeline.query.chat`). Kept here so the LLM-related strings live
# alongside their translation table.
LOCALE_LANGUAGE_NAME = {"ja": "日本語", "en": "English"}


# ---------------------------------------------------------------------------
# Tool handlers
# ---------------------------------------------------------------------------


def _apply_date_overrides(ctx: RangeCtx, args: dict) -> RangeCtx:
    """Translate days_back / from / to in tool args into a derived RangeCtx.

    Precedence: explicit ``from``/``to`` win over ``days_back``; if neither is
    present the original ctx is returned unchanged.
    """
    days_back = args.get("days_back")
    # Accept both key forms: canonicalize writes "from_date"/"to_date";
    # legacy/LLM-direct callers may pass "from"/"to" (no underscores).
    raw_from = args.get("from_date") or args.get("from")
    raw_to = args.get("to_date") or args.get("to")
    if days_back is None and not raw_from and not raw_to:
        return ctx

    today = jst_today()

    def _parse(s: Any) -> date | None:
        if not isinstance(s, str):
            return None
        try:
            return date.fromisoformat(s)
        except ValueError:
            return None

    if raw_from or raw_to:
        new_to = _parse(raw_to) or today
        new_from = _parse(raw_from) or new_to - timedelta(days=29)
    else:
        # Reaching this branch implies days_back is set: the early return
        # above already handled (days_back is None and no raw dates).
        assert days_back is not None
        try:
            n = max(1, int(days_back))
        except (TypeError, ValueError):
            return ctx
        new_to = today
        new_from = today - timedelta(days=n - 1)

    if new_from > new_to:
        new_from, new_to = new_to, new_from
    if (new_to - new_from).days >= MAX_RANGE_DAYS:
        new_from = new_to - timedelta(days=MAX_RANGE_DAYS - 1)
    return replace(ctx, from_date=new_from, to_date=new_to)


async def _is_route_registered(route: str | None, conn, agency_id: int) -> bool:
    """Is the route_code listed in the agency's static GTFS at all?

    This is the long-lived registry check: it asks "does the agency operate
    this route?" and is true regardless of whether the requested time window
    contains observations. The previous implementation conflated registry
    membership with same-period data presence — so a perfectly valid route
    surfaced "登録されていない" whenever the chosen window happened to be
    empty (e.g. right after a TRUNCATE). Keep registry separate from period
    data so the user sees the correct guidance.
    """
    if not route:
        return False
    row = await conn.fetchrow(
        "SELECT 1 FROM static_routes "
        "WHERE agency_id=$1 "
        "  AND regexp_replace(route_id, '.*\\((\\d+)\\)$', '\\1') = $2 "
        "LIMIT 1",
        agency_id,
        str(route),
    )
    if row is not None:
        return True
    # Fallback: some agencies may not have static_routes loaded yet but do
    # have observations. Treat any historical observation as proof the route
    # exists, even outside the selected window.
    row = await conn.fetchrow(
        "SELECT 1 FROM updates WHERE agency_id=$1 AND route_code=$2 LIMIT 1",
        agency_id,
        str(route),
    )
    return row is not None


async def _tool_route_stats(args: dict, ctx: RangeCtx, conn, agency_id: int, locale: str) -> ToolResult:
    route = args.get("route")
    if not route:
        return ToolResult(kind="empty", summary=_summary("route_arg_required", lang=locale))
    if not await _is_route_registered(route, conn, agency_id):
        return ToolResult(
            kind="empty",
            summary=_summary("route_not_registered", lang=locale, route=route, agency_id=agency_id),
        )
    rows = await route_dow_breakdown(agency_id, ctx, conn, route=str(route))
    if not rows:
        return ToolResult(
            kind="empty",
            summary=_summary(
                "route_no_data",
                lang=locale,
                route=route,
                from_date=ctx.from_date,
                to_date=ctx.to_date,
            ),
        )
    # Render ISODOW int as locale-appropriate label so the LLM sees '月' / 'Mon' not '1'.
    rendered = [[r[0], r[1], dow_label(r[2], lang=locale), r[3], r[4]] for r in rows]
    return ToolResult(
        kind="table",
        summary=_summary("route_summary", lang=locale, route=route),
        rows=rendered,
        columns=["route_code", "service_type", "dow", "avg_min", "samples"],
    )


_SERVICE_TYPE_MAP: dict[str, ServiceType] = {
    "weekday": "平日",
    "weekend": "土日祝",
    "all": "all",
}


async def _tool_top_n(args: dict, ctx: RangeCtx, conn, agency_id: int, locale: str) -> ToolResult:
    metric = args.get("metric", "avg_delay")
    # BUG-2 fix: card chips send "k" (matches gold eval canonical form); LLM
    # direct calls use "n" (matches the TOOLS JSON schema).  Accept both, with
    # "k" taking precedence so the user's slider value is always honoured.
    n = int(args.get("k", args.get("n", 10)))
    best_first = bool(args.get("best_first", metric == "on_time_rate"))

    # BUG-1 fix: if the chip/LLM supplies service_type, narrow ctx.service so
    # compute_ranking / compute_worst_5min honour the filter.
    raw_service = args.get("service_type")
    if raw_service and raw_service in _SERVICE_TYPE_MAP:
        ctx = replace(ctx, service=_SERVICE_TYPE_MAP[raw_service])

    if metric == "avg_delay":
        sort_order = "asc" if best_first else "desc"
        rows = await compute_ranking(agency_id, ctx, conn, sort_order=sort_order, limit=n)
        cols = ["route_code", "service_type", "avg_min", "p50_min", "p90_min", "samples"]
        label = _summary(
            "label_ranking_ontime" if best_first else "label_ranking_delay",
            lang=locale,
        )
    elif metric == "on_time_rate":
        # BUG-3 fix: pass sort_order so best_first=False yields worst routes (ASC).
        sort_order = "desc" if best_first else "asc"
        rows = await compute_on_time(agency_id, ctx, conn, limit=n, sort_order=sort_order)
        cols = ["route_code", "service_type", "on_time_pct", "avg_min", "samples"]
        label = _summary("label_ranking_ontime_rate", lang=locale)
    elif metric == "worst_5min":
        rows = await compute_worst_5min(agency_id, ctx, conn, limit=n)
        cols = ["route_code", "service_type", "late5_count", "avg_min", "samples"]
        label = _summary("label_ranking_late5", lang=locale)
    else:
        return ToolResult(kind="empty", summary=_summary("unknown_metric", lang=locale, metric=metric))

    if not rows:
        return ToolResult(kind="empty", summary=_summary("no_data", lang=locale))

    # For on_time_rate with best_first=False, show "下位N路線" to make clear
    # the result is the worst routes, not the best.
    summary_key = "ranking_summary_worst" if metric == "on_time_rate" and not best_first else "ranking_summary"
    return ToolResult(
        kind="table",
        summary=_summary(summary_key, lang=locale, label=label, count=len(rows)),
        rows=[list(r) for r in rows],
        columns=cols,
    )


async def _tool_compare_segments(args: dict, ctx: RangeCtx, conn, agency_id: int, locale: str) -> ToolResult:
    dimension = args.get("dimension", "dow")
    route = args.get("route")

    if dimension == "dow":
        # When the LLM scopes to a single route, push it into ctx so the
        # compute function actually narrows the query (the post-filter on a
        # top-50 result was missing routes outside the top).
        cmp_ctx = replace(ctx, routes=(str(route),)) if route else ctx
        rows = await compute_compare_ranking(agency_id, cmp_ctx, conn, limit=50)
        if not rows:
            return ToolResult(
                kind="empty",
                summary=_summary("compare_no_data", lang=locale),
            )
        return ToolResult(
            kind="table",
            summary=_summary("compare_summary_dow", lang=locale),
            rows=[list(r) for r in rows],
            columns=["route_code", "heijitsu_min", "kyujitsu_min", "abs_delta", "signed_delta"],
        )

    if dimension == "service_type":
        if not route:
            return ToolResult(
                kind="empty",
                summary=_summary("compare_service_needs_route", lang=locale),
            )
        rows = await route_compare_service(agency_id, ctx, conn, route=str(route))
        if not rows:
            return ToolResult(
                kind="empty",
                summary=_summary("compare_route_no_data", lang=locale, route=route),
            )
        return ToolResult(
            kind="table",
            summary=_summary("compare_summary_service", lang=locale, route=route),
            rows=[list(r) for r in rows],
            columns=["service_type", "avg_min", "samples"],
        )

    return ToolResult(kind="empty", summary=_summary("unknown_dimension", lang=locale, dimension=dimension))


def _weighted_avg_min(days: list[dict]) -> float | None:
    """Sample-weighted mean delay across trend buckets — the exact pooled mean.

    Each `days` entry is already a per-bucket sample-weighted mean with a
    `samples` weight, so a plain mean-of-means would overweight thin days (one
    sparse outlier day could dominate the headline). Null-`avg_min` days are
    skipped (not counted as 0). Returns None when there are no measured samples.
    """
    num = 0.0
    den = 0
    for d in days:
        v = d.get("avg_min")
        if v is None:
            continue
        s = d.get("samples") or 0
        num += v * s
        den += s
    return num / den if den else None


async def _tool_time_series(args: dict, ctx: RangeCtx, conn, agency_id: int, locale: str) -> ToolResult:
    # When the LLM scopes to a single route, push it into ctx so the
    # compute function narrows the trend to that route only. Without this
    # the dispatch shim resolves the route alias but the compute call
    # ignores it (compute_trend_series doesn't take a route arg directly).
    route = args.get("route")
    series_ctx = replace(ctx, routes=(str(route),)) if route else ctx
    # BUG-4 fix: read granularity from args (default "day") and forward it.
    granularity = args.get("granularity", "day")
    series = await compute_trend_series(agency_id, series_ctx, conn, granularity=granularity)
    days = series.get("days") or []
    if not days:
        return ToolResult(kind="empty", summary=_summary("trend_no_data", lang=locale))
    avg = _weighted_avg_min(days)
    if avg is None:
        return ToolResult(kind="empty", summary=_summary("trend_no_data", lang=locale))
    return ToolResult(
        kind="series",
        summary=_summary(
            "trend_summary",
            lang=locale,
            from_date=ctx.from_date,
            to_date=ctx.to_date,
            avg=avg,
        ),
        series=days,
    )


async def _tool_on_time_rate(args: dict, ctx: RangeCtx, conn, agency_id: int, locale: str) -> ToolResult:
    threshold_min = int(args.get("threshold_min", 1))
    threshold_sec = max(0, threshold_min) * 60
    # BUG-2 fix: card chips send "k"; LLM direct calls send "n".  Accept both.
    n = int(args.get("k", args.get("n", 20)))
    rows = await compute_on_time(agency_id, ctx, conn, threshold_sec=threshold_sec, limit=n)
    if not rows:
        return ToolResult(kind="empty", summary=_summary("on_time_no_data", lang=locale))
    return ToolResult(
        kind="table",
        summary=_summary(
            "on_time_summary",
            lang=locale,
            threshold_min=threshold_min,
            count=len(rows),
        ),
        rows=[list(r) for r in rows],
        columns=["route_code", "service_type", "on_time_pct", "avg_min", "samples"],
    )


async def _tool_route_meta(args: dict, ctx: RangeCtx, conn, agency_id: int, locale: str) -> ToolResult:
    route = args.get("route")
    if not route:
        return ToolResult(kind="empty", summary=_summary("route_arg_required", lang=locale))
    r = await route_info(agency_id, conn, route=str(route))
    if r is None:
        return ToolResult(kind="empty", summary=_summary("route_meta_not_found", lang=locale, route=route))
    dash = _summary("dash", lang=locale)
    pairs = [
        (_summary("meta_label_name", lang=locale), r[1] or dash),
        (
            _summary("meta_label_stops", lang=locale),
            _summary("meta_stops_value", lang=locale, n=r[2]) if r[2] is not None else dash,
        ),
        (_summary("meta_label_first", lang=locale), r[3] or dash),
        (_summary("meta_label_last", lang=locale), r[4] or dash),
        (
            _summary("meta_label_trips", lang=locale),
            _summary("meta_trips_value", lang=locale, n=r[5]) if r[5] is not None else dash,
        ),
    ]
    return ToolResult(
        kind="kv",
        summary=_summary("route_meta_summary", lang=locale, route=route),
        pairs=pairs,
    )


_HANDLERS = {
    "route_stats": _tool_route_stats,
    "top_n": _tool_top_n,
    "compare_segments": _tool_compare_segments,
    "time_series": _tool_time_series,
    "on_time_rate": _tool_on_time_rate,
    "route_meta": _tool_route_meta,
}

# Same identity-preserving pattern as TOOLS above: mutate the existing
# dict so callers holding a pre-merge reference still see the meta
# handlers without re-importing.
_HANDLERS.update(META_HANDLERS)


async def dispatch(
    tool_name: str,
    arguments: dict[str, Any],
    ctx: RangeCtx,
    conn,
    agency_id: int,
    locale: str = "ja",
) -> ToolResult:
    """Run the named tool. Unknown tool → empty ToolResult with explanation.

    Tool args may include ``days_back``/``from``/``to`` to override the UI
    range; ``_apply_date_overrides`` produces a derived ctx that all handlers
    consume. The original UI ctx is otherwise preserved (DOW / time_band /
    routes / service still apply). ``locale`` selects the language for the
    human-readable ``summary`` field on the returned :class:`ToolResult`.
    """
    # BUG-1 fix: card-alias tools sent by the frontend map to canonical handler
    # names. Keep aliases explicit here so the eval gold file can use the short
    # card names (on_time / trend / cmp_service) and live dispatch still works.
    _TOOL_ALIASES: dict[str, str] = {
        "on_time": "on_time_rate",
        "trend": "time_series",
        "cmp_service": "compare_segments",
    }
    tool_name = _TOOL_ALIASES.get(tool_name, tool_name)

    async with perf.timed_block(f"ask.tool.{tool_name}"):
        # Card templates use "route_code" as the arg name (matches the param
        # definition), but all handlers read "route".  Normalise before dispatch.
        # "k" → "n" remapping is intentionally NOT done here — handlers accept both
        # (BUG-2 fix).
        if "route_code" in arguments and "route" not in arguments:
            raw_rc = arguments["route_code"]
            arguments = {k: v for k, v in arguments.items() if k != "route_code"}
            arguments = {"route": raw_rc, **arguments}

        handler = _HANDLERS.get(tool_name)
        if handler is None:
            return ToolResult(kind="empty", summary=_summary("unsupported_tool", lang=locale, name=tool_name))

        from pipeline.query.schema_linker import resolve_route

        if tool_name in {"route_stats", "compare_segments", "route_meta", "time_series"}:
            raw_route = arguments.get("route")
            if raw_route:
                resolution = await resolve_route(str(raw_route), conn, agency_id)
                if resolution.route_code is not None:
                    arguments = {**arguments, "route": resolution.route_code}
                elif resolution.candidates:
                    cand_txt = " / ".join(
                        _summary("did_you_mean_candidate", lang=locale, code=code, name=name)
                        for code, name in resolution.candidates[:5]
                    )
                    return ToolResult(
                        kind="empty",
                        summary=_summary(
                            "route_did_you_mean",
                            lang=locale,
                            raw=raw_route,
                            candidates=cand_txt,
                        ),
                    )

        effective_ctx = _apply_date_overrides(ctx, arguments)
        return await handler(arguments, effective_ctx, conn, agency_id, locale)


# ---------------------------------------------------------------------------
# Rendering — the assistant bubble's text body
# ---------------------------------------------------------------------------


def render_tool_result(result: ToolResult, locale: str = "ja") -> str:
    """Compact locale-aware text rendering of a :class:`ToolResult`."""
    if result.kind == "empty" or result.kind == "text":
        return result.summary

    lines = [f"【{result.summary}】"]

    if result.kind == "table":
        for i, row in enumerate(result.rows[:30], 1):
            cells = [str(c) if c is not None else "—" for c in row]
            lines.append(f"{i}. " + " / ".join(cells))
        if len(result.rows) > 30:
            lines.append(_summary("more_rows", lang=locale, n=len(result.rows) - 30))
        return "\n".join(lines)

    if result.kind == "series":
        for d in result.series[:30]:
            top = d.get("top_offenders") or []
            top_txt = ""
            if top:
                routes = ", ".join(_summary("route_prefix", lang=locale, route=t["route_code"]) for t in top[:3])
                top_txt = _summary("series_top_offenders", lang=locale, routes=routes)
            lines.append(
                _summary(
                    "series_line",
                    lang=locale,
                    date=d["date"],
                    avg=d.get("avg_min", 0),
                    samples=d.get("samples", 0),
                    top=top_txt,
                )
            )
        return "\n".join(lines)

    if result.kind == "kv":
        for k, v in result.pairs:
            lines.append(f"{k}: {v}")
        return "\n".join(lines)

    return result.summary
