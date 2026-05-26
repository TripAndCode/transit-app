"""Meta-tools for the Ask tab: deterministic answers to data-availability
questions ("どんな路線がある？" "いつから？") that the analytic 6-tool
surface used to fail on with random tool calls.

Two tools:

* ``describe_data(kind, limit?, filter_substring?)`` — generic SQL-backed
  enumeration. ``kind`` is the only required arg.
* ``capabilities(category?)`` — curated list of example questions.

Both produce :class:`ToolResult` objects so the chat renderer is
unchanged. Localized summaries follow the existing ``_chat_str`` pattern;
all DB queries are scoped to the request's ``agency_id`` except
``kind="agencies"``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from api.range import RangeCtx

if TYPE_CHECKING:  # pragma: no cover — annotation-only
    from pipeline.query.tools import ToolResult


def _ToolResult(*args, **kwargs):
    """Lazy proxy for :class:`pipeline.query.tools.ToolResult`.

    Importing ``ToolResult`` at module load time creates a circular import
    once :mod:`pipeline.query.tools` re-imports ``META_TOOLS``/``META_HANDLERS``
    from this module. If ``meta_tools`` is imported first (e.g. by a test
    module that targets it directly), the eager import re-enters a
    half-initialized ``meta_tools`` and explodes at collection time. Defer the
    import until first call so module initialization order is irrelevant.
    """
    from pipeline.query.tools import ToolResult as _TR

    return _TR(*args, **kwargs)


VALID_KINDS = (
    "routes",
    "stops",
    "date_range",
    "agencies",
    "sample_counts",
    "overview",
    "metrics",
)


def _summary(text_jp: str, text_en: str, locale: str) -> str:
    return text_en if locale == "en" else text_jp


async def describe_data(
    args: dict[str, Any],
    ctx: RangeCtx,
    conn,
    agency_id: int,
    locale: str = "ja",
) -> ToolResult:
    kind = args.get("kind")
    # Defensive coercion: the LLM occasionally hands back ``limit`` as a
    # string ("abc", "50") or even ``None`` despite the JSON-schema
    # constraint. Coerce to int, fall back to the default rather than
    # raising — a free-text refusal would be far worse than a slightly
    # wider result set.
    raw_limit = args.get("limit", 50)
    try:
        limit = max(1, min(int(raw_limit) if raw_limit is not None else 50, 200))
    except (TypeError, ValueError):
        limit = 50

    if kind not in VALID_KINDS:
        return _ToolResult(
            kind="empty",
            summary=_summary(
                f"未知の kind: {kind}。有効値: {', '.join(VALID_KINDS)}",
                f"unknown kind: {kind}. valid: {', '.join(VALID_KINDS)}",
                locale,
            ),
        )

    if kind == "routes":
        substring = args.get("filter_substring")
        if substring:
            rows = await conn.fetch(
                "SELECT regexp_replace(route_id, '.*\\((\\d+)\\)$', '\\1') AS code, "
                "       route_short_name "
                "FROM static_routes "
                "WHERE agency_id = $1 AND route_short_name ILIKE '%' || $2 || '%' "
                "ORDER BY route_short_name "
                "LIMIT $3",
                agency_id,
                substring,
                limit,
            )
            total = await conn.fetchval(
                "SELECT COUNT(*) FROM static_routes "
                "WHERE agency_id = $1 AND route_short_name ILIKE '%' || $2 || '%'",
                agency_id,
                substring,
            )
            # A non-empty filter that matches nothing must NOT fall through to
            # a full dump (the original bug) nor claim "0件表示" beside a high
            # unfiltered total. Return an unambiguous empty result instead.
            if total == 0:
                return _ToolResult(
                    kind="empty",
                    summary=_summary(
                        f"「{substring}」に該当する路線がありません。",
                        f"no matching routes for '{substring}'.",
                        locale,
                    ),
                )
            return _ToolResult(
                kind="table",
                summary=_summary(
                    f"「{substring}」に一致する路線: {total} 件（先頭 {len(rows)} 件を表示）",
                    f"routes matching '{substring}': {total} (showing first {len(rows)})",
                    locale,
                ),
                rows=[[r["code"], r["route_short_name"]] for r in rows],
                columns=["route_code", "route_short_name"],
            )
        rows = await conn.fetch(
            "SELECT regexp_replace(route_id, '.*\\((\\d+)\\)$', '\\1') AS code, "
            "       route_short_name "
            "FROM static_routes "
            "WHERE agency_id = $1 "
            "ORDER BY route_short_name "
            "LIMIT $2",
            agency_id,
            limit,
        )
        total = await conn.fetchval("SELECT COUNT(*) FROM static_routes WHERE agency_id = $1", agency_id)
        if total == 0:
            return _ToolResult(
                kind="empty",
                summary=_summary(
                    "このエージェンシーには路線が登録されていません。",
                    "no routes registered for this agency.",
                    locale,
                ),
            )
        return _ToolResult(
            kind="table",
            summary=_summary(
                f"このエージェンシーには {total} 路線あります（先頭 {len(rows)} 件を表示）",
                f"This agency has {total} routes (showing first {len(rows)})",
                locale,
            ),
            rows=[[r["code"], r["route_short_name"]] for r in rows],
            columns=["route_code", "route_short_name"],
        )

    if kind == "stops":
        substring = args.get("filter_substring")
        if substring:
            rows = await conn.fetch(
                "SELECT stop_id, stop_name FROM static_stops "
                "WHERE agency_id = $1 AND stop_name ILIKE '%' || $2 || '%' "
                "ORDER BY stop_name LIMIT $3",
                agency_id,
                substring,
                limit,
            )
        else:
            rows = await conn.fetch(
                "SELECT stop_id, stop_name FROM static_stops WHERE agency_id = $1 ORDER BY stop_name LIMIT $2",
                agency_id,
                limit,
            )
        total = await conn.fetchval("SELECT COUNT(*) FROM static_stops WHERE agency_id = $1", agency_id)
        if total == 0:
            return _ToolResult(
                kind="empty",
                summary=_summary(
                    "このエージェンシーには停留所が登録されていません。",
                    "no stops registered for this agency.",
                    locale,
                ),
            )
        return _ToolResult(
            kind="table",
            summary=_summary(
                f"このエージェンシーには {total} 停留所あります（先頭 {len(rows)} 件）",
                f"This agency has {total} stops (showing first {len(rows)})",
                locale,
            ),
            rows=[[r["stop_id"], r["stop_name"]] for r in rows],
            columns=["stop_id", "stop_name"],
        )

    if kind == "date_range":
        row = await conn.fetchrow(
            "SELECT MIN(captured_at) AS first_obs, "
            "       MAX(captured_at) AS last_obs, "
            "       COUNT(DISTINCT captured_at::date) AS days, "
            "       COUNT(*) AS rows_n "
            "FROM updates WHERE agency_id = $1",
            agency_id,
        )
        if row is None or row["first_obs"] is None:
            return _ToolResult(
                kind="empty",
                summary=_summary("観測データがありません。", "no observations.", locale),
            )
        pairs = [
            ("first_observed", row["first_obs"].isoformat()),
            ("last_observed", row["last_obs"].isoformat()),
            ("distinct_days", str(row["days"])),
            ("total_rows", str(row["rows_n"])),
        ]
        return _ToolResult(
            kind="kv",
            summary=_summary(
                f"観測期間: {row['first_obs'].date()} 〜 {row['last_obs'].date()}",
                f"observation window: {row['first_obs'].date()} – {row['last_obs'].date()}",
                locale,
            ),
            pairs=pairs,
        )

    if kind == "agencies":
        # Multi-tenant data-isolation default: unless the caller explicitly
        # opts in to cross-agency mode, only return the caller's own agency.
        # The LLM might be tempted to list every tenant in response to
        # "どんなエージェンシーがある?" — that's a leak waiting to happen.
        cross_agency = bool(args.get("cross_agency", False))
        if cross_agency:
            rows = await conn.fetch("SELECT agency_id, agency_name FROM agencies ORDER BY agency_id")
        else:
            rows = await conn.fetch(
                "SELECT agency_id, agency_name FROM agencies WHERE agency_id = $1 ORDER BY agency_id",
                agency_id,
            )
        return _ToolResult(
            kind="table",
            summary=_summary(
                f"登録されているエージェンシー: {len(rows)} 社",
                f"registered agencies: {len(rows)}",
                locale,
            ),
            rows=[[r["agency_id"], r["agency_name"]] for r in rows],
            columns=["agency_id", "agency_name"],
        )

    if kind == "sample_counts":
        # "サンプルが少ない系統" wants the least-sampled routes, so allow an
        # ascending order. Validate against an allowlist — never interpolate
        # raw LLM input into the ORDER BY clause.
        order = str(args.get("order", "desc")).lower()
        if order not in ("desc", "asc"):
            order = "desc"
        direction = "ASC" if order == "asc" else "DESC"
        rows = await conn.fetch(
            "SELECT route_code, COUNT(*) AS samples "
            "FROM updates "
            "WHERE agency_id = $1 "
            "  AND captured_at::date BETWEEN $2 AND $3 "
            "GROUP BY route_code "
            f"ORDER BY samples {direction} "
            "LIMIT $4",
            agency_id,
            ctx.from_date,
            ctx.to_date,
            limit,
        )
        if not rows:
            return _ToolResult(
                kind="empty",
                summary=_summary(
                    f"選択期間 ({ctx.from_date}〜{ctx.to_date}) にサンプルデータがありません。",
                    f"no sample data in the selected window ({ctx.from_date} – {ctx.to_date}).",
                    locale,
                ),
            )
        # Clamp the DISPLAYED window so the summary never claims coverage past
        # where data actually exists. The BETWEEN above is unchanged; we only
        # adjust the text. MAX(captured_at) is over the requested window so a
        # NULL means no data fell in range.
        data_end = await conn.fetchval(
            "SELECT MAX(captured_at)::date FROM updates "
            "WHERE agency_id = $1 AND captured_at::date BETWEEN $2 AND $3",
            agency_id,
            ctx.from_date,
            ctx.to_date,
        )
        window_end = data_end if (data_end is not None and data_end < ctx.to_date) else ctx.to_date
        if order == "asc":
            jp = f"サンプル数の少ない順 {len(rows)}系統 ({ctx.from_date}〜{window_end})"
            en = f"sample count bottom-{len(rows)} ({ctx.from_date} – {window_end})"
        else:
            jp = f"サンプル数 上位{len(rows)}系統 ({ctx.from_date}〜{window_end})"
            en = f"sample count top-{len(rows)} ({ctx.from_date} – {window_end})"
        return _ToolResult(
            kind="table",
            summary=_summary(jp, en, locale),
            rows=[[r["route_code"], int(r["samples"])] for r in rows],
            columns=["route_code", "samples"],
        )

    if kind == "overview":
        route_count = await conn.fetchval("SELECT COUNT(*) FROM static_routes WHERE agency_id = $1", agency_id)
        stop_count = await conn.fetchval("SELECT COUNT(*) FROM static_stops WHERE agency_id = $1", agency_id)
        obs_row = await conn.fetchrow(
            "SELECT COUNT(*) AS n, MIN(captured_at) AS first_obs, MAX(captured_at) AS last_obs "
            "FROM updates WHERE agency_id = $1",
            agency_id,
        )
        pairs = [
            ("routes", str(route_count)),
            ("stops", str(stop_count)),
            ("observations", str(obs_row["n"])),
            (
                "first_observed",
                obs_row["first_obs"].isoformat() if obs_row["first_obs"] else "—",
            ),
            (
                "last_observed",
                obs_row["last_obs"].isoformat() if obs_row["last_obs"] else "—",
            ),
        ]
        return _ToolResult(
            kind="kv",
            summary=_summary("データセット概要", "dataset overview", locale),
            pairs=pairs,
        )

    if kind == "metrics":
        if locale == "en":
            metric_list = [
                ("avg_delay", "average delay (min)"),
                ("p50_min", "median delay (min)"),
                ("p90_min", "90th percentile delay (min)"),
                ("on_time_pct", "on-time rate (%) — default threshold 60 s"),
                ("late5_pct", "share of >5-minute delays (%)"),
                ("samples", "observation sample count"),
            ]
        else:
            metric_list = [
                ("avg_delay", "平均遅延 (分)"),
                ("p50_min", "中央値遅延 (分)"),
                ("p90_min", "90 パーセンタイル遅延 (分)"),
                ("on_time_pct", "定時率 (%) — 既定しきい値 60 秒"),
                ("late5_pct", "5分超過率 (%)"),
                ("samples", "観測サンプル数"),
            ]
        return _ToolResult(
            kind="kv",
            summary=_summary("計算可能な指標の一覧", "available metrics", locale),
            pairs=metric_list,
        )

    # Unreachable — VALID_KINDS gate caught it.
    return _ToolResult(kind="empty", summary="impossible")


_CAPABILITY_EXAMPLES_JP = {
    "single_route": "系統22171の遅延 / 系統16071のp90 / A1の運行情報",
    "ranking": "遅延ワースト10 / 定時率TOP5 / 5分超過の多い系統",
    "comparison": "平日と土日祝の比較 / 22171の種別比較 / 系統間の差",
    "trend": "直近2週間の傾向 / 日次トレンド / 推移を見せて",
    "on_time": "5分以内の定時率 / 定時率ランキング / しきい値別の率",
    "stop_level": "(現状未対応:Phase 3) 停留所単位の集計",
    "meta": "どんな路線がある？ / いつからのデータ？ / サンプル数の多い系統",
}

_CAPABILITY_EXAMPLES_EN = {
    "single_route": "route 22171 delay / route 16071 p90 / route info for A1",
    "ranking": "worst-10 delays / on-time top-5 / most >5min delays",
    "comparison": "weekday vs weekend / service-type split for 22171 / route deltas",
    "trend": "last-14d trend / daily series / show the trend",
    "on_time": "on-time rate within 5min / on-time ranking / by threshold",
    "stop_level": "(not yet supported: Phase 3) per-stop aggregation",
    "meta": "what routes exist? / since when do we have data? / top routes by samples",
}


async def capabilities(
    args: dict[str, Any],
    ctx: RangeCtx,
    conn,
    agency_id: int,
    locale: str = "ja",
) -> ToolResult:
    table = _CAPABILITY_EXAMPLES_EN if locale == "en" else _CAPABILITY_EXAMPLES_JP
    requested = args.get("category")
    if requested and requested in table:
        pairs = [(requested, table[requested])]
    else:
        pairs = list(table.items())
    return _ToolResult(
        kind="kv",
        summary=_summary(
            "答えられる質問の例（カテゴリ別）",
            "example questions I can answer (by category)",
            locale,
        ),
        pairs=pairs,
    )


META_TOOLS: list[dict] = [
    {
        "type": "function",
        "function": {
            "name": "describe_data",
            "description": (
                "Answer 'what data do you have?'-class questions deterministically. "
                "Use whenever the user asks about routes/stops the dataset contains, "
                "data freshness, sample counts, or a general dataset overview. "
                "Prefer this over guessing with route_meta or route_stats when the user "
                "did NOT specify a route. Examples in Japanese: "
                "「どんな路線がある？」→kind=routes, 「いつから？」→kind=date_range, "
                "「サンプル数の多い系統」→kind=sample_counts, 「全体感」→kind=overview."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "kind": {
                        "type": "string",
                        "enum": list(VALID_KINDS),
                    },
                    "limit": {"type": "integer", "minimum": 1, "maximum": 200},
                    "filter_substring": {"type": "string"},
                    "order": {
                        "type": "string",
                        "enum": ["desc", "asc"],
                        "description": (
                            "Only honored when kind='sample_counts'. Default 'desc' → "
                            "most-sampled routes first. Use 'asc' for the LEAST-sampled "
                            "routes (e.g. 「サンプル数の少ない系統」/「データが薄い系統」)."
                        ),
                    },
                    "cross_agency": {
                        "type": "boolean",
                        "description": (
                            "Only honored when kind='agencies'. Default false → return "
                            "ONLY the caller's own agency. Set true to list every "
                            "agency in the system; do this only when the user has "
                            "explicit cross-tenant authority (very rare)."
                        ),
                    },
                },
                "required": ["kind"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "capabilities",
            "description": (
                "Return a curated list of example questions the assistant can answer. "
                "Use this when the user's question is vague (「やばい系統」「いつものやつ」), "
                "out of scope, or when you cannot map their question to any analytic tool. "
                "Prefer this over refusing in free text."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "category": {
                        "type": "string",
                        "enum": [
                            "single_route",
                            "ranking",
                            "comparison",
                            "trend",
                            "on_time",
                            "stop_level",
                            "meta",
                        ],
                    },
                },
            },
        },
    },
]


META_HANDLERS = {
    "describe_data": describe_data,
    "capabilities": capabilities,
}
