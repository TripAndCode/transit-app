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

    return ToolResult(kind="empty", summary=_summary(
        f"kind={kind} は未実装です。", f"kind={kind} not yet implemented.", locale
    ))
