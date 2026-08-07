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

from datetime import datetime, timezone
from typing import Any
from zoneinfo import ZoneInfo

from api.range import RangeCtx
from pipeline.query.results import ToolResult

_JST = ZoneInfo("Asia/Tokyo")

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


def _as_utc(dt: datetime | None) -> datetime | None:
    """Attach UTC tzinfo to a ClickHouse-returned naive datetime — see
    api/routers/map.py's ``_as_utc`` for the full rationale (ClickHouse's
    `updates.captured_at` is UTC, but clickhouse-connect returns naive
    values, unlike asyncpg's tz-aware `timestamptz`)."""
    if dt is None or dt.tzinfo is not None:
        return dt
    return dt.replace(tzinfo=timezone.utc)


async def describe_data(
    args: dict[str, Any],
    ctx: RangeCtx,
    conn,
    agency_id: int,
    locale: str = "ja",
    ch=None,
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

    # Offset for "show me more" pagination. Same defensive coercion as limit:
    # the LLM may pass a string or None. Negative offsets clamp to 0 rather
    # than letting Postgres reject them.
    raw_offset = args.get("offset", 0)
    try:
        offset = max(0, int(raw_offset) if raw_offset is not None else 0)
    except (TypeError, ValueError):
        offset = 0

    if kind not in VALID_KINDS:
        return ToolResult(
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
                "ORDER BY route_short_name, route_id "
                "LIMIT $3 OFFSET $4",
                agency_id,
                substring,
                limit,
                offset,
            )
            total = await conn.fetchval(
                "SELECT COUNT(*) FROM static_routes WHERE agency_id = $1 AND route_short_name ILIKE '%' || $2 || '%'",
                agency_id,
                substring,
            )
            # A non-empty filter that matches nothing must NOT fall through to
            # a full dump (the original bug) nor claim "0件表示" beside a high
            # unfiltered total. Return an unambiguous empty result instead.
            if total == 0:
                return ToolResult(
                    kind="empty",
                    summary=_summary(
                        f"「{substring}」に該当する路線がありません。",
                        f"no matching routes for '{substring}'.",
                        locale,
                    ),
                )
            if offset > 0 and rows:
                shown_from = offset + 1
                shown_to = offset + len(rows)
                summary = _summary(
                    f"「{substring}」に一致する全{total}路線中 "
                    f"{shown_from}–{shown_to}件を表示（続きは「次の{limit}件」）",
                    f"routes matching '{substring}' {shown_from}–{shown_to} of {total} (next: 'next {limit}')",
                    locale,
                )
            else:
                summary = _summary(
                    f"「{substring}」に一致する路線: {total} 件（先頭 {len(rows)} 件を表示）",
                    f"routes matching '{substring}': {total} (showing first {len(rows)})",
                    locale,
                )
            return ToolResult(
                kind="table",
                summary=summary,
                rows=[[r["code"], r["route_short_name"]] for r in rows],
                columns=["route_code", "route_short_name"],
            )
        rows = await conn.fetch(
            "SELECT regexp_replace(route_id, '.*\\((\\d+)\\)$', '\\1') AS code, "
            "       route_short_name "
            "FROM static_routes "
            "WHERE agency_id = $1 "
            "ORDER BY route_short_name, route_id "
            "LIMIT $2 OFFSET $3",
            agency_id,
            limit,
            offset,
        )
        total = await conn.fetchval("SELECT COUNT(*) FROM static_routes WHERE agency_id = $1", agency_id)
        if total == 0:
            return ToolResult(
                kind="empty",
                summary=_summary(
                    "このエージェンシーには路線が登録されていません。",
                    "no routes registered for this agency.",
                    locale,
                ),
            )
        if offset > 0 and rows:
            shown_from = offset + 1
            shown_to = offset + len(rows)
            summary = _summary(
                f"全{total}路線中 {shown_from}–{shown_to}件を表示（続きは「次の{limit}件」）",
                f"routes {shown_from}–{shown_to} of {total} (next: 'next {limit}')",
                locale,
            )
        else:
            summary = _summary(
                f"このエージェンシーには {total} 路線あります（先頭 {len(rows)} 件を表示）",
                f"This agency has {total} routes (showing first {len(rows)})",
                locale,
            )
        return ToolResult(
            kind="table",
            summary=summary,
            rows=[[r["code"], r["route_short_name"]] for r in rows],
            columns=["route_code", "route_short_name"],
        )

    if kind == "stops":
        substring = args.get("filter_substring")
        if substring:
            rows = await conn.fetch(
                "SELECT stop_id, stop_name FROM static_stops "
                "WHERE agency_id = $1 AND stop_name ILIKE '%' || $2 || '%' "
                "ORDER BY stop_name, stop_id LIMIT $3 OFFSET $4",
                agency_id,
                substring,
                limit,
                offset,
            )
        else:
            rows = await conn.fetch(
                "SELECT stop_id, stop_name FROM static_stops "
                "WHERE agency_id = $1 ORDER BY stop_name, stop_id LIMIT $2 OFFSET $3",
                agency_id,
                limit,
                offset,
            )
        total = await conn.fetchval("SELECT COUNT(*) FROM static_stops WHERE agency_id = $1", agency_id)
        if total == 0:
            return ToolResult(
                kind="empty",
                summary=_summary(
                    "このエージェンシーには停留所が登録されていません。",
                    "no stops registered for this agency.",
                    locale,
                ),
            )
        if offset > 0 and rows:
            shown_from = offset + 1
            shown_to = offset + len(rows)
            summary = _summary(
                f"全{total}停留所中 {shown_from}–{shown_to}件を表示（続きは「次の{limit}件」）",
                f"stops {shown_from}–{shown_to} of {total} (next: 'next {limit}')",
                locale,
            )
        else:
            summary = _summary(
                f"このエージェンシーには {total} 停留所あります（先頭 {len(rows)} 件）",
                f"This agency has {total} stops (showing first {len(rows)})",
                locale,
            )
        return ToolResult(
            kind="table",
            summary=summary,
            rows=[[r["stop_id"], r["stop_name"]] for r in rows],
            columns=["stop_id", "stop_name"],
        )

    if kind == "date_range":
        if ch is None:
            return ToolResult(
                kind="empty",
                summary=_summary(
                    "ClickHouseに接続できないため取得できません。",
                    "unable to fetch — ClickHouse is unavailable.",
                    locale,
                ),
            )
        # toDate(captured_at, 'Asia/Tokyo') — NOT a bare toDate(captured_at) —
        # matches the JST civil day every Postgres connection touching
        # `updates` has always bucketed by (SET TIME ZONE 'Asia/Tokyo').
        result = await ch.query(
            "SELECT minOrNull(captured_at) AS first_obs, "
            "       maxOrNull(captured_at) AS last_obs, "
            "       COUNT(DISTINCT toDate(captured_at, 'Asia/Tokyo')) AS days, "
            "       COUNT(*) AS rows_n "
            "FROM updates WHERE agency_id = {agency_id:UInt16}",
            parameters={"agency_id": agency_id},
        )
        first_obs, last_obs, days, rows_n = result.result_rows[0]
        if first_obs is None:
            return ToolResult(
                kind="empty",
                summary=_summary("観測データがありません。", "no observations.", locale),
            )
        first_obs = _as_utc(first_obs)
        last_obs = _as_utc(last_obs)
        assert first_obs is not None and last_obs is not None  # first_obs None-check above guards both
        pairs = [
            ("first_observed", first_obs.isoformat()),
            ("last_observed", last_obs.isoformat()),
            ("distinct_days", str(days)),
            ("total_rows", str(rows_n)),
        ]
        return ToolResult(
            kind="kv",
            summary=_summary(
                f"観測期間: {first_obs.date()} 〜 {last_obs.date()}",
                f"observation window: {first_obs.date()} – {last_obs.date()}",
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
            rows = await conn.fetch(
                "SELECT agency_id, agency_name FROM agencies WHERE deleted_at IS NULL ORDER BY agency_id"
            )
        else:
            rows = await conn.fetch(
                "SELECT agency_id, agency_name FROM agencies WHERE agency_id = $1 AND deleted_at IS NULL "
                "ORDER BY agency_id",
                agency_id,
            )
        return ToolResult(
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
        if ch is None:
            return ToolResult(
                kind="empty",
                summary=_summary(
                    "ClickHouseに接続できないため取得できません。",
                    "unable to fetch — ClickHouse is unavailable.",
                    locale,
                ),
            )
        # "サンプルが少ない路線" wants the least-sampled routes, so allow an
        # ascending order. Validate against an allowlist — never interpolate
        # raw LLM input into the ORDER BY clause.
        order = str(args.get("order", "desc")).lower()
        if order not in ("desc", "asc"):
            order = "desc"
        direction = "ASC" if order == "asc" else "DESC"
        # toDate(captured_at, 'Asia/Tokyo') — the JST civil day, matching
        # every Postgres connection's `captured_at::date` under
        # SET TIME ZONE 'Asia/Tokyo'.
        ch_result = await ch.query(
            f"""
            SELECT route_code, count() AS samples
            FROM updates
            WHERE agency_id = {{agency_id:UInt16}}
              AND toDate(captured_at, 'Asia/Tokyo') BETWEEN {{from_date:Date}} AND {{to_date:Date}}
            GROUP BY route_code
            ORDER BY samples {direction}, route_code
            LIMIT {{limit:UInt32}} OFFSET {{offset:UInt32}}
            """,
            parameters={
                "agency_id": agency_id,
                "from_date": ctx.from_date,
                "to_date": ctx.to_date,
                "limit": limit,
                "offset": offset,
            },
        )
        rows = [{"route_code": rc, "samples": n} for rc, n in ch_result.result_rows]
        if not rows:
            return ToolResult(
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
        data_end_ch = await ch.query(
            "SELECT maxOrNull(captured_at) FROM updates "
            "WHERE agency_id = {agency_id:UInt16} "
            "  AND toDate(captured_at, 'Asia/Tokyo') BETWEEN {from_date:Date} AND {to_date:Date}",
            parameters={"agency_id": agency_id, "from_date": ctx.from_date, "to_date": ctx.to_date},
        )
        data_end_ts = _as_utc(data_end_ch.result_rows[0][0])
        data_end = data_end_ts.astimezone(_JST).date() if data_end_ts is not None else None
        window_end = data_end if (data_end is not None and data_end < ctx.to_date) else ctx.to_date
        if offset > 0:
            total_ch = await ch.query(
                "SELECT COUNT(DISTINCT route_code) FROM updates "
                "WHERE agency_id = {agency_id:UInt16} "
                "  AND toDate(captured_at, 'Asia/Tokyo') BETWEEN {from_date:Date} AND {to_date:Date}",
                parameters={"agency_id": agency_id, "from_date": ctx.from_date, "to_date": ctx.to_date},
            )
            total = total_ch.result_rows[0][0]
            shown_from = offset + 1
            shown_to = offset + len(rows)
            if order == "asc":
                jp = (
                    f"サンプル数の少ない順 全{total}路線中 {shown_from}–{shown_to}件を表示"
                    f"（続きは「次の{limit}件」）({ctx.from_date}〜{window_end})"
                )
                en = (
                    f"sample count ascending {shown_from}–{shown_to} of {total} "
                    f"(next: 'next {limit}') ({ctx.from_date} – {window_end})"
                )
            else:
                jp = (
                    f"サンプル数 全{total}路線中 {shown_from}–{shown_to}件を表示"
                    f"（続きは「次の{limit}件」）({ctx.from_date}〜{window_end})"
                )
                en = (
                    f"sample count {shown_from}–{shown_to} of {total} "
                    f"(next: 'next {limit}') ({ctx.from_date} – {window_end})"
                )
        elif order == "asc":
            jp = f"サンプル数の少ない順 {len(rows)}路線 ({ctx.from_date}〜{window_end})"
            en = f"sample count bottom-{len(rows)} ({ctx.from_date} – {window_end})"
        else:
            jp = f"サンプル数 上位{len(rows)}路線 ({ctx.from_date}〜{window_end})"
            en = f"sample count top-{len(rows)} ({ctx.from_date} – {window_end})"
        return ToolResult(
            kind="table",
            summary=_summary(jp, en, locale),
            rows=[[r["route_code"], int(r["samples"])] for r in rows],
            columns=["route_code", "samples"],
        )

    if kind == "overview":
        if ch is None:
            return ToolResult(
                kind="empty",
                summary=_summary(
                    "ClickHouseに接続できないため取得できません。",
                    "unable to fetch — ClickHouse is unavailable.",
                    locale,
                ),
            )
        route_count = await conn.fetchval("SELECT COUNT(*) FROM static_routes WHERE agency_id = $1", agency_id)
        stop_count = await conn.fetchval("SELECT COUNT(*) FROM static_stops WHERE agency_id = $1", agency_id)
        obs_result = await ch.query(
            "SELECT COUNT(*) AS n, minOrNull(captured_at) AS first_obs, maxOrNull(captured_at) AS last_obs "
            "FROM updates WHERE agency_id = {agency_id:UInt16}",
            parameters={"agency_id": agency_id},
        )
        obs_n, obs_first, obs_last = obs_result.result_rows[0]
        obs_first = _as_utc(obs_first)
        obs_last = _as_utc(obs_last)
        pairs = [
            ("routes", str(route_count)),
            ("stops", str(stop_count)),
            ("observations", str(obs_n)),
            (
                "first_observed",
                obs_first.isoformat() if obs_first else "—",
            ),
            (
                "last_observed",
                obs_last.isoformat() if obs_last else "—",
            ),
        ]
        return ToolResult(
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
        return ToolResult(
            kind="kv",
            summary=_summary("計算可能な指標の一覧", "available metrics", locale),
            pairs=metric_list,
        )

    # Unreachable — VALID_KINDS gate caught it.
    return ToolResult(kind="empty", summary="impossible")


_CAPABILITY_EXAMPLES_JP = {
    "single_route": "路線22171の遅延 / 路線16071のp90 / A1の運行情報",
    "ranking": "遅延ワースト10 / 定時率TOP5 / 5分超過の多い路線",
    "comparison": "平日と土日祝の比較 / 22171の種別比較 / 路線間の差",
    "trend": "直近2週間の傾向 / 日次トレンド / 推移を見せて",
    "on_time": "5分以内の定時率 / 定時率ランキング / しきい値別の率",
    "stop_level": "(現状未対応:Phase 3) 停留所単位の集計",
    "meta": "どんな路線がある？ / いつからのデータ？ / サンプル数の多い路線",
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
    ch=None,
) -> ToolResult:
    table = _CAPABILITY_EXAMPLES_EN if locale == "en" else _CAPABILITY_EXAMPLES_JP
    requested = args.get("category")
    if requested and requested in table:
        pairs = [(requested, table[requested])]
    else:
        pairs = list(table.items())
    return ToolResult(
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
                "「サンプル数の多い路線」→kind=sample_counts, 「全体感」→kind=overview."
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
                            "routes (e.g. 「サンプル数の少ない路線」/「データが薄い路線」)."
                        ),
                    },
                    "offset": {
                        "type": "integer",
                        "minimum": 0,
                        "description": (
                            "Row offset for pagination; for a 'next page' follow-up, re-call with offset += limit."
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
                "Use this when the user's question is vague (「やばい路線」「いつものやつ」), "
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
