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
* :func:`render_tool_result` — produce a Japanese summary string the
  frontend can show in the assistant bubble.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import date, timedelta
from typing import Any, Literal

from api.range import RangeCtx
from pipeline.query.executor import execute as _legacy_execute
from pipeline.reports import (
    compute_compare_ranking,
    compute_on_time,
    compute_ranking,
    compute_trend_series,
    compute_worst_5min,
)

TopNMetric = Literal["avg_delay", "on_time_rate", "worst_5min"]


# ---------------------------------------------------------------------------
# Result type
# ---------------------------------------------------------------------------


@dataclass
class ToolResult:
    """Discriminated union (by ``kind``) returned to the API layer."""

    kind: Literal["table", "series", "kv", "empty", "text"]
    summary_jp: str
    rows: list = field(default_factory=list)
    columns: list[str] = field(default_factory=list)
    series: list = field(default_factory=list)
    pairs: list = field(default_factory=list)


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
                "doing (e.g. '系統5の遅延', '44372はどう?'). Always returns "
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


SYSTEM_PROMPT = """\
あなたは青森市バスの遅延分析アシスタントです。利用可能なツールを使って質問に答えます。

== 重要なルール ==
1. ツールが質問に合うなら必ずツールを呼び出す。前置きや説明文は不要。
2. **route 引数は実際のシステム route_code(4〜5桁の数字、例: '16071', '22171')のみ。**
   ユーザーが '系統5' のような短い数字や '雨天' のような単語を route として渡してきた場合、
   ツールを呼ばず、日本語で「'<入力>' は系統コードではない可能性があります」と説明し、
   類似の答えられる質問を 2〜3 件提案する。
3. データの提供範囲外の質問(天気、運賃、事故、車両情報など)はツールを呼ばず、
   利用できるデータを伝え、関連する答えられる質問を 2〜3 件提案する。
4. **期間の上書き**: ユーザーが「直近X日/週/月」「過去N日」「先週」「先月」「昨日」など
   特定の期間を明示した場合、`days_back` (整数日) または `from`/`to` (YYYY-MM-DD) 引数で
   ツールに渡してUIのデフォルト範囲を上書きする。指定がなければ何も渡さない(UIの範囲が使われる)。
   - 「直近2週間の傾向」→ time_series(days_back=14)
   - 「先月の定時率」→ on_time_rate(days_back=30) (シンプルに30日と解釈)
   - 「過去3日の遅延」→ top_n(metric='avg_delay', n=10, days_back=3)
5. 曜日/時間帯フィルタはツール引数で上書きする必要はない(UIで適用済み)。
6. 出力は日本語のみ。

== 利用可能なツール ==
- route_stats(route, days_back?, from?, to?): 1 系統の遅延統計
- top_n(metric, n?, best_first?, days_back?, from?, to?): 全系統ランキング
- compare_segments(route?, dimension, days_back?, from?, to?): 平日 vs 土日祝などの比較
- time_series(route?, days_back?, from?, to?): 日次トレンド
- on_time_rate(threshold_min?, n?, days_back?, from?, to?): 定時率ランキング
- route_meta(route): 系統の路線情報

== 例 ==
- "今日の遅延ランキング" → top_n(metric='avg_delay', n=10)
- "直近2週間の傾向" → time_series(days_back=14)
- "系統22171の先週の遅延" → route_stats(route='22171', days_back=7)
- "過去3日で5分超が一番多い系統" → top_n(metric='worst_5min', n=10, days_back=3)
- "雨天時の比較" → ツール呼ばず、「天気データはありません。
  代わりに『22171の平日と土日祝の比較』が答えられます」と返す
"""


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _route_name_lookup(route: str | None) -> str:
    return f"系統{route}" if route else "（系統指定なし）"


# ---------------------------------------------------------------------------
# Tool handlers
# ---------------------------------------------------------------------------


def _apply_date_overrides(ctx: RangeCtx, args: dict) -> RangeCtx:
    """Translate days_back / from / to in tool args into a derived RangeCtx.

    Precedence: explicit ``from``/``to`` win over ``days_back``; if neither is
    present the original ctx is returned unchanged.
    """
    days_back = args.get("days_back")
    raw_from = args.get("from")
    raw_to = args.get("to")
    if days_back is None and not raw_from and not raw_to:
        return ctx

    today = date.today()

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
        try:
            n = max(1, int(days_back))
        except (TypeError, ValueError):
            return ctx
        new_to = today
        new_from = today - timedelta(days=n - 1)

    if new_from > new_to:
        new_from, new_to = new_to, new_from
    return replace(ctx, from_date=new_from, to_date=new_to)


async def _validate_route_exists(route: str | None, conn, agency_id: int, ctx: RangeCtx) -> bool:
    """Cheap existence check scoped to the request window.

    A route last seen six months ago should NOT validate as live — otherwise
    the friendly 'no observations in selected period' branch is unreachable
    and the user gets a misleading empty table instead of guidance.
    """
    if not route or not str(route).isdigit():
        return False
    row = await conn.fetchrow(
        "SELECT 1 FROM updates WHERE agency_id=$1 AND route_code=$2   AND captured_at::date BETWEEN $3 AND $4 LIMIT 1",
        agency_id,
        str(route),
        ctx.from_date,
        ctx.to_date,
    )
    return row is not None


async def _tool_route_stats(args: dict, ctx: RangeCtx, conn, agency_id: int) -> ToolResult:
    route = args.get("route")
    if not route:
        return ToolResult(kind="empty", summary_jp="route 引数が必要です。")
    if not await _validate_route_exists(route, conn, agency_id, ctx):
        return ToolResult(
            kind="empty",
            summary_jp=f"'{route}' は登録されている系統コードではありません。例: 16071, 22171。",
        )
    intent = {"query_type": "by_dow", "route": str(route), "limit": 50}
    rows = await _legacy_execute(intent, conn, agency_id) or []
    if not rows:
        return ToolResult(
            kind="empty",
            summary_jp=f"系統{route} の集計データが選択期間にありません。",
        )
    return ToolResult(
        kind="table",
        summary_jp=f"系統{route} の遅延サマリ",
        rows=[list(r) for r in rows],
        columns=["route_code", "service_type", "dow", "avg_min", "samples"],
    )


async def _tool_top_n(args: dict, ctx: RangeCtx, conn, agency_id: int) -> ToolResult:
    metric = args.get("metric", "avg_delay")
    n = int(args.get("n", 10))
    best_first = bool(args.get("best_first", metric == "on_time_rate"))

    if metric == "avg_delay":
        sort_order = "asc" if best_first else "desc"
        rows = await compute_ranking(agency_id, ctx, conn, sort_order=sort_order, limit=n)
        cols = ["route_code", "service_type", "avg_min", "p50_min", "p90_min", "samples"]
        label = "定時運行" if best_first else "遅延"
    elif metric == "on_time_rate":
        rows = await compute_on_time(agency_id, ctx, conn, limit=n)
        cols = ["route_code", "service_type", "on_time_pct", "avg_min", "samples"]
        label = "定時率"
    elif metric == "worst_5min":
        rows = await compute_worst_5min(agency_id, ctx, conn, limit=n)
        cols = ["route_code", "service_type", "late5_count", "avg_min", "samples"]
        label = "5分以上遅延件数"
    else:
        return ToolResult(kind="empty", summary_jp=f"未知の metric: {metric}")

    if not rows:
        return ToolResult(kind="empty", summary_jp="データがありません。")

    return ToolResult(
        kind="table",
        summary_jp=f"{label}ランキング 上位{len(rows)}系統",
        rows=[list(r) for r in rows],
        columns=cols,
    )


async def _tool_compare_segments(args: dict, ctx: RangeCtx, conn, agency_id: int) -> ToolResult:
    dimension = args.get("dimension", "dow")
    route = args.get("route")

    if dimension == "dow":
        rows = await compute_compare_ranking(agency_id, ctx, conn, limit=50)
        if route:
            rows = [r for r in rows if str(r[0]) == str(route)]
        if not rows:
            return ToolResult(
                kind="empty",
                summary_jp="比較に必要なデータがありません。",
            )
        return ToolResult(
            kind="table",
            summary_jp="平日 vs 土日祝 遅延比較",
            rows=[list(r) for r in rows],
            columns=["route_code", "heijitsu_min", "kyujitsu_min", "abs_delta", "signed_delta"],
        )

    if dimension == "service_type":
        if not route:
            return ToolResult(
                kind="empty",
                summary_jp="dimension=service_type の場合は route が必要です。",
            )
        intent = {"query_type": "compare", "route": str(route)}
        rows = await _legacy_execute(intent, conn, agency_id) or []
        if not rows:
            return ToolResult(kind="empty", summary_jp=f"系統{route} の比較データなし。")
        return ToolResult(
            kind="table",
            summary_jp=f"系統{route} 種別比較",
            rows=[list(r) for r in rows],
            columns=["route_code", "heijitsu_min", "kyujitsu_min", "samples"],
        )

    return ToolResult(kind="empty", summary_jp=f"未知の dimension: {dimension}")


async def _tool_time_series(args: dict, ctx: RangeCtx, conn, agency_id: int) -> ToolResult:
    series = await compute_trend_series(agency_id, ctx, conn)
    days = series.get("days") or []
    if not days:
        return ToolResult(kind="empty", summary_jp="期間内に観測データがありません。")
    avg = sum((d["avg_min"] or 0) for d in days) / len(days)
    return ToolResult(
        kind="series",
        summary_jp=f"日次トレンド ({ctx.from_date}〜{ctx.to_date}): 平均{avg:.2f}分",
        series=days,
    )


async def _tool_on_time_rate(args: dict, ctx: RangeCtx, conn, agency_id: int) -> ToolResult:
    threshold_min = int(args.get("threshold_min", 1))
    threshold_sec = max(0, threshold_min) * 60
    n = int(args.get("n", 20))
    rows = await compute_on_time(agency_id, ctx, conn, threshold_sec=threshold_sec, limit=n)
    if not rows:
        return ToolResult(kind="empty", summary_jp="定時率を計算できるデータがありません。")
    return ToolResult(
        kind="table",
        summary_jp=f"定時率 (遅延 {threshold_min} 分以内) 上位{len(rows)}系統",
        rows=[list(r) for r in rows],
        columns=["route_code", "service_type", "on_time_pct", "avg_min", "samples"],
    )


async def _tool_route_meta(args: dict, ctx: RangeCtx, conn, agency_id: int) -> ToolResult:
    route = args.get("route")
    if not route:
        return ToolResult(kind="empty", summary_jp="route 引数が必要です。")
    intent = {"query_type": "route_info", "route": str(route)}
    rows = await _legacy_execute(intent, conn, agency_id) or []
    if not rows:
        return ToolResult(kind="empty", summary_jp=f"系統{route} の路線情報が見つかりません。")
    r = rows[0]
    pairs = [
        ("路線名", r[1] or "—"),
        ("停留所数", f"{r[2]}駅" if r[2] is not None else "—"),
        ("始発", r[3] or "—"),
        ("最終", r[4] or "—"),
        ("運行便数", f"{r[5]}便" if r[5] is not None else "—"),
    ]
    return ToolResult(
        kind="kv",
        summary_jp=f"系統{route} 路線情報",
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


async def dispatch(
    tool_name: str,
    arguments: dict[str, Any],
    ctx: RangeCtx,
    conn,
    agency_id: int,
) -> ToolResult:
    """Run the named tool. Unknown tool → empty ToolResult with explanation.

    Tool args may include ``days_back``/``from``/``to`` to override the UI
    range; ``_apply_date_overrides`` produces a derived ctx that all handlers
    consume. The original UI ctx is otherwise preserved (DOW / time_band /
    routes / service still apply).
    """
    handler = _HANDLERS.get(tool_name)
    if handler is None:
        return ToolResult(kind="empty", summary_jp=f"未対応のツール: {tool_name}")
    effective_ctx = _apply_date_overrides(ctx, arguments)
    return await handler(arguments, effective_ctx, conn, agency_id)


# ---------------------------------------------------------------------------
# Rendering — the assistant bubble's text body
# ---------------------------------------------------------------------------


def render_tool_result(result: ToolResult) -> str:
    """Compact Japanese text rendering of a :class:`ToolResult`."""
    if result.kind == "empty" or result.kind == "text":
        return result.summary_jp

    lines = [f"【{result.summary_jp}】"]

    if result.kind == "table":
        for i, row in enumerate(result.rows[:30], 1):
            cells = [str(c) if c is not None else "—" for c in row]
            lines.append(f"{i}. " + " / ".join(cells))
        if len(result.rows) > 30:
            lines.append(f"…他{len(result.rows) - 30}件")
        return "\n".join(lines)

    if result.kind == "series":
        for d in result.series[:30]:
            top = d.get("top_offenders") or []
            top_txt = ""
            if top:
                top_txt = " (悪化: " + ", ".join(f"系統{t['route_code']}" for t in top[:3]) + ")"
            lines.append(f"{d['date']}: 平均{d.get('avg_min', 0):.2f}分 / {d.get('samples', 0)}件{top_txt}")
        return "\n".join(lines)

    if result.kind == "kv":
        for k, v in result.pairs:
            lines.append(f"{k}: {v}")
        return "\n".join(lines)

    return result.summary_jp
