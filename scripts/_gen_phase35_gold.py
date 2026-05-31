"""Generate Phase ③.5 parameterized-card gold entries.

Run from repo root:
    poetry run python scripts/_gen_phase35_gold.py > tests/ask_eval/gold_questions.jsonl
"""

from __future__ import annotations

import json
from datetime import date

from pipeline.query.intent import canonicalize

CTX = {"from_date": date(2026, 5, 1), "to_date": date(2026, 5, 30)}


def entry(eid: str, tool: str, args: dict) -> dict:
    can = canonicalize(tool, args, CTX)
    return {
        "id": eid,
        "via": "builder",
        "question": f"[card] {eid}",
        "expected_tool": tool,
        "expected_args_canonical": can,
    }


ENTRIES = [
    # top_delay (tool=top_n, fixed_args={metric:"avg_delay"})
    entry("top_delay_k5_all", "top_n", {"metric": "avg_delay", "k": 5, "service_type": "all"}),
    entry("top_delay_k10_weekday", "top_n", {"metric": "avg_delay", "k": 10, "service_type": "weekday"}),
    entry("top_delay_k3_weekend", "top_n", {"metric": "avg_delay", "k": 3, "service_type": "weekend"}),
    entry("top_delay_k20_all", "top_n", {"metric": "avg_delay", "k": 20, "service_type": "all"}),
    # ontime_rank (tool=on_time)
    entry("ontime_top5_worst", "on_time", {"k": 5, "best_first": False}),
    entry("ontime_top5_best", "on_time", {"k": 5, "best_first": True}),
    entry("ontime_top10_worst", "on_time", {"k": 10, "best_first": False}),
    entry("ontime_top3_best", "on_time", {"k": 3, "best_first": True}),
    # route_trend (tool=trend, fixed_args={metric:"avg_delay"})
    entry("trend_route1_day", "trend", {"metric": "avg_delay", "route_code": "1", "granularity": "day"}),
    entry("trend_route1_week", "trend", {"metric": "avg_delay", "route_code": "1", "granularity": "week"}),
    entry("trend_route1_month", "trend", {"metric": "avg_delay", "route_code": "1", "granularity": "month"}),
    entry("trend_routeA_week", "trend", {"metric": "avg_delay", "route_code": "A", "granularity": "week"}),
    # weekday_vs_weekend (tool=cmp_service, fixed_args={metric:"avg_delay"})
    entry("cmp_service_route1", "cmp_service", {"metric": "avg_delay", "route_code": "1"}),
    entry("cmp_service_routeA", "cmp_service", {"metric": "avg_delay", "route_code": "A"}),
    entry("cmp_service_route2", "cmp_service", {"metric": "avg_delay", "route_code": "2"}),
    entry("cmp_service_route3", "cmp_service", {"metric": "avg_delay", "route_code": "3"}),
    # route_overview (tool=route_stats)
    entry("route_stats_route1", "route_stats", {"route_code": "1"}),
    entry("route_stats_routeA", "route_stats", {"route_code": "A"}),
    entry("route_stats_route2", "route_stats", {"route_code": "2"}),
    entry("route_stats_route3", "route_stats", {"route_code": "3"}),
]

if __name__ == "__main__":
    for e in ENTRIES:
        print(json.dumps(e, ensure_ascii=False))
