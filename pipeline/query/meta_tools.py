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

from typing import Any

from api.range import RangeCtx
from pipeline.query.tools import ToolResult


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
    limit = max(1, min(int(args.get("limit", 50) or 50), 200))

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
        rows = await conn.fetch(
            "SELECT regexp_replace(route_id, '.*\\((\\d+)\\)$', '\\1') AS code, "
            "       route_short_name "
            "FROM static_routes "
            "WHERE agency_id = $1 "
            "ORDER BY route_id "
            "LIMIT $2",
            agency_id,
            limit,
        )
        total = await conn.fetchval(
            "SELECT COUNT(*) FROM static_routes WHERE agency_id = $1", agency_id
        )
        return ToolResult(
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
                "ORDER BY stop_id LIMIT $3",
                agency_id, substring, limit,
            )
        else:
            rows = await conn.fetch(
                "SELECT stop_id, stop_name FROM static_stops "
                "WHERE agency_id = $1 ORDER BY stop_id LIMIT $2",
                agency_id, limit,
            )
        total = await conn.fetchval(
            "SELECT COUNT(*) FROM static_stops WHERE agency_id = $1", agency_id
        )
        return ToolResult(
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
            return ToolResult(
                kind="empty",
                summary=_summary("観測データがありません。", "no observations.", locale),
            )
        pairs = [
            ("first_observed", row["first_obs"].isoformat()),
            ("last_observed", row["last_obs"].isoformat()),
            ("distinct_days", str(row["days"])),
            ("total_rows", str(row["rows_n"])),
        ]
        return ToolResult(
            kind="kv",
            summary=_summary(
                f"観測期間: {row['first_obs'].date()} 〜 {row['last_obs'].date()}",
                f"observation window: {row['first_obs'].date()} – {row['last_obs'].date()}",
                locale,
            ),
            pairs=pairs,
        )

    if kind == "agencies":
        rows = await conn.fetch(
            "SELECT agency_id, agency_name FROM agencies ORDER BY agency_id"
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
        rows = await conn.fetch(
            "SELECT route_code, COUNT(*) AS samples "
            "FROM updates "
            "WHERE agency_id = $1 "
            "  AND captured_at::date BETWEEN $2 AND $3 "
            "GROUP BY route_code "
            "ORDER BY samples DESC "
            "LIMIT $4",
            agency_id, ctx.from_date, ctx.to_date, limit,
        )
        return ToolResult(
            kind="table",
            summary=_summary(
                f"サンプル数 上位{len(rows)}系統 ({ctx.from_date}〜{ctx.to_date})",
                f"sample count top-{len(rows)} ({ctx.from_date} – {ctx.to_date})",
                locale,
            ),
            rows=[[r["route_code"], int(r["samples"])] for r in rows],
            columns=["route_code", "samples"],
        )

    if kind == "overview":
        route_count = await conn.fetchval(
            "SELECT COUNT(*) FROM static_routes WHERE agency_id = $1", agency_id
        )
        stop_count = await conn.fetchval(
            "SELECT COUNT(*) FROM static_stops WHERE agency_id = $1", agency_id
        )
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
        return ToolResult(
            kind="kv",
            summary=_summary("データセット概要", "dataset overview", locale),
            pairs=pairs,
        )

    if kind == "metrics":
        metric_list = [
            ("avg_delay", "平均遅延 (分)"),
            ("p50_min",   "中央値遅延 (分)"),
            ("p90_min",   "90 パーセンタイル遅延 (分)"),
            ("on_time_pct", "定時率 (%) — 既定しきい値 60 秒"),
            ("late5_pct", "5分超過率 (%)"),
            ("samples",   "観測サンプル数"),
        ]
        return ToolResult(
            kind="kv",
            summary=_summary(
                "計算可能な指標の一覧", "available metrics", locale
            ),
            pairs=metric_list,
        )

    # Unreachable — VALID_KINDS gate caught it.
    return ToolResult(kind="empty", summary="impossible")
