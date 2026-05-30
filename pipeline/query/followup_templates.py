"""Per-tool follow-up generators used by the active-thread UI.

After each chip dispatch, the frontend renders a small set of "next obvious
moves" — opposite ranking, swap time window, drill into the top result, etc.
These are static templates parameterized by the just-completed dispatch's
``(tool, args)`` + (optionally) the first row of the result.

Follow-ups never invoke the LLM; they're deterministic transformations.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

# A follow-up dict shape returned to the frontend:
#   {"id": str, "title_ja": str, "title_en": str, "tool": str, "args": dict}


@dataclass(frozen=True)
class FollowupTemplate:
    id: str
    title_ja: str
    title_en: str
    builder: Callable[[dict[str, Any], list | None], dict[str, Any]]  # produces (tool, args) dict


def _swap_best_first(args: dict[str, Any], _row: list | None) -> dict[str, Any]:
    new_args = dict(args)
    new_args["best_first"] = not new_args.get("best_first", False)
    new_args["n"] = 5
    return {"tool": "top_n", "args": new_args}


def _tw(args: dict[str, Any], tw: str) -> dict[str, Any]:
    new_args = {k: v for k, v in args.items() if k not in ("from_date", "to_date")}
    new_args["time_window"] = tw
    return new_args


def _next_page(args: dict[str, Any], _row: list | None) -> dict[str, Any]:
    new_args = dict(args)
    new_args["offset"] = int(new_args.get("offset", 0) or 0) + int(new_args.get("limit", 50) or 50)
    return {"tool": "describe_data", "args": new_args}


def _gran(args: dict[str, Any], g: str) -> dict[str, Any]:
    new_args = dict(args)
    new_args["granularity"] = g
    return {"tool": "time_series", "args": new_args}


FOLLOWUPS: dict[str, list[FollowupTemplate]] = {
    "top_n": [
        FollowupTemplate(
            "opposite",
            "↕ 逆順に切り替え",
            "↕ Flip ranking",
            lambda a, r: _swap_best_first(a, r),
        ),
        FollowupTemplate(
            "tw-2weeks",
            "📅 直近2週間に変更",
            "📅 Switch to last 2 weeks",
            lambda a, r: {"tool": "top_n", "args": _tw(a, "last_2_weeks")},
        ),
        FollowupTemplate(
            "tw-30days",
            "📅 直近30日に変更",
            "📅 Switch to last 30 days",
            lambda a, r: {"tool": "top_n", "args": _tw(a, "last_30_days")},
        ),
        FollowupTemplate(
            "compare-dow",
            "⚖️ 平日/土日で比較",
            "⚖️ Compare weekday vs weekend",
            lambda a, r: {"tool": "compare_segments", "args": {"dimension": "dow"}},
        ),
    ],
    "time_series": [
        FollowupTemplate(
            "gran-day",
            "📅 日別に切り替え",
            "📅 Switch to daily",
            lambda a, r: _gran(a, "day"),
        ),
        FollowupTemplate(
            "gran-week",
            "📅 週別に切り替え",
            "📅 Switch to weekly",
            lambda a, r: _gran(a, "week"),
        ),
        FollowupTemplate(
            "tw-2weeks",
            "📆 直近2週間に絞る",
            "📆 Limit to last 2 weeks",
            lambda a, r: {"tool": "time_series", "args": _tw(a, "last_2_weeks")},
        ),
        FollowupTemplate(
            "compare-dow",
            "⚖️ 平日/土日で比較",
            "⚖️ Compare weekday vs weekend",
            lambda a, r: {"tool": "compare_segments", "args": {"dimension": "dow"}},
        ),
    ],
    "compare_segments": [
        FollowupTemplate(
            "by-service",
            "⚖️ 便種別で比較",
            "⚖️ Compare by service type",
            lambda a, r: {"tool": "compare_segments", "args": {"dimension": "service_type"}},
        ),
        FollowupTemplate(
            "rank-top",
            "🏆 ランキングを見る",
            "🏆 See top-N ranking",
            lambda a, r: {"tool": "top_n", "args": {"metric": "avg_delay", "n": 10}},
        ),
    ],
    "route_stats": [
        FollowupTemplate(
            "route-trend",
            "📈 この路線の時系列",
            "📈 Trend for this route",
            lambda a, r: {"tool": "time_series", "args": {k: v for k, v in a.items() if k.startswith("route")}},
        ),
        FollowupTemplate(
            "compare-dow",
            "⚖️ 平日/土日で比較",
            "⚖️ Compare weekday vs weekend",
            lambda a, r: {"tool": "compare_segments", "args": {"dimension": "dow"}},
        ),
    ],
    "describe_data": [
        FollowupTemplate(
            "next-page",
            "▸ 次の50件",
            "▸ Next 50",
            lambda a, r: _next_page(a, r),
        ),
        FollowupTemplate(
            "date-range",
            "📅 データの期間を見る",
            "📅 See date range",
            lambda a, r: {"tool": "describe_data", "args": {"kind": "date_range"}},
        ),
    ],
}


def generate_followups(
    tool: str,
    args: dict[str, Any],
    ctx: dict[str, Any],
    result_first_row: list | None = None,
) -> list[dict[str, Any]]:
    """Return up to 5 follow-up suggestions for the just-completed dispatch."""
    templates = FOLLOWUPS.get(tool, [])
    out: list[dict[str, Any]] = []
    for t in templates[:5]:
        try:
            new = t.builder(args, result_first_row)
        except Exception:
            continue
        if not new or "tool" not in new:
            continue
        out.append(
            {
                "id": t.id,
                "title_ja": t.title_ja,
                "title_en": t.title_en,
                "tool": new["tool"],
                "args": new["args"],
            }
        )
    return out
