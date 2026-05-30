"""Static registry of the 26 guided-query chip templates shown in the Ask tab.

Each chip is a (tool, args) tuple plus localized titles. ``builder_required=True``
means tapping the chip opens the structured builder pre-populated with the
chip's args (used for tools that need a route_id input we can't pick for
the user).
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class ChipTemplate:
    id: str
    category: str  # 'meta' | 'ranking' | 'trend' | 'compare' | 'detail'
    title_ja: str
    title_en: str
    tool: str
    args: dict[str, Any] = field(default_factory=dict)
    builder_required: bool = False


CHIPS: list[ChipTemplate] = [
    # --- メタ情報 (4) ---
    ChipTemplate(
        "meta-routes",
        "meta",
        "路線一覧",
        "Route list",
        "describe_data",
        {"kind": "routes"},
    ),
    ChipTemplate(
        "meta-stops",
        "meta",
        "停留所数",
        "Stop count",
        "describe_data",
        {"kind": "stops"},
    ),
    ChipTemplate(
        "meta-date-range",
        "meta",
        "データの期間",
        "Date range",
        "describe_data",
        {"kind": "date_range"},
    ),
    ChipTemplate(
        "meta-sample-counts",
        "meta",
        "サンプル数の概況",
        "Sample counts",
        "describe_data",
        {"kind": "sample_counts"},
    ),
    # --- ランキング (6) ---
    ChipTemplate(
        "rank-delay-top",
        "ranking",
        "遅延ランキングTOP10",
        "Top 10 by avg delay",
        "top_n",
        {"metric": "avg_delay", "n": 10},
    ),
    ChipTemplate(
        "rank-delay-worst",
        "ranking",
        "遅延ワースト5",
        "Worst 5 by avg delay",
        "top_n",
        {"metric": "avg_delay", "n": 5, "best_first": False},
    ),
    ChipTemplate(
        "rank-ontime-top",
        "ranking",
        "定時率トップ10",
        "Top 10 by on-time rate",
        "top_n",
        {"metric": "on_time_rate", "n": 10, "best_first": True},
    ),
    ChipTemplate(
        "rank-5min-top",
        "ranking",
        "5分超 遅延が多い路線TOP10",
        "Top 10 by 5+ min delays",
        "top_n",
        {"metric": "worst_5min", "n": 10},
    ),
    ChipTemplate(
        "rank-weekday",
        "ranking",
        "平日の遅延ランキングTOP10",
        "Weekday top 10 delay",
        "top_n",
        {"metric": "avg_delay", "n": 10, "service_type": "weekday"},
    ),
    ChipTemplate(
        "rank-weekend",
        "ranking",
        "土日祝の遅延ランキングTOP10",
        "Weekend top 10 delay",
        "top_n",
        {"metric": "avg_delay", "n": 10, "service_type": "weekend"},
    ),
    # --- 時系列・トレンド (5) ---
    ChipTemplate(
        "trend-daily",
        "trend",
        "日別の遅延推移",
        "Daily delay trend",
        "time_series",
        {"granularity": "day"},
    ),
    ChipTemplate(
        "trend-weekly",
        "trend",
        "週別の遅延推移",
        "Weekly delay trend",
        "time_series",
        {"granularity": "week"},
    ),
    ChipTemplate(
        "trend-monthly",
        "trend",
        "月別の遅延推移",
        "Monthly delay trend",
        "time_series",
        {"granularity": "month"},
    ),
    ChipTemplate(
        "trend-2weeks",
        "trend",
        "直近2週間の遅延傾向",
        "Last 2 weeks trend",
        "time_series",
        {"time_window": "last_2_weeks"},
    ),
    ChipTemplate(
        "trend-30days",
        "trend",
        "直近30日の遅延傾向",
        "Last 30 days trend",
        "time_series",
        {"time_window": "last_30_days"},
    ),
    # --- 比較 (3) ---
    ChipTemplate(
        "cmp-dow",
        "compare",
        "平日と土日祝の比較",
        "Weekday vs weekend",
        "compare_segments",
        {"dimension": "dow"},
    ),
    ChipTemplate(
        "cmp-service",
        "compare",
        "便種別の比較",
        "By service type",
        "compare_segments",
        {"dimension": "service_type"},
    ),
    ChipTemplate(
        "cmp-period",
        "compare",
        "前2週間との比較",
        "vs previous 2 weeks",
        "compare_segments",
        {"dimension": "period", "time_window": "last_2_weeks"},
    ),
    # --- 路線・停留所詳細 (3, builder-required) ---
    ChipTemplate(
        "route-stats",
        "detail",
        "路線を選んで遅延統計を見る",
        "Per-route delay stats",
        "route_stats",
        {},
        builder_required=True,
    ),
    ChipTemplate(
        "route-meta",
        "detail",
        "路線を選んで運行情報を見る",
        "Per-route metadata",
        "route_meta",
        {},
        builder_required=True,
    ),
    ChipTemplate(
        "route-trend",
        "detail",
        "路線を選んで時系列を見る",
        "Per-route time series",
        "time_series",
        {},
        builder_required=True,
    ),
    # --- 5 more chips to reach 26: useful variants ---
    # ランキング (1 more) — top by 5min on weekday
    ChipTemplate(
        "rank-5min-weekday",
        "ranking",
        "平日 5分超 多い路線TOP10",
        "Weekday top 10 by 5+ min",
        "top_n",
        {"metric": "worst_5min", "n": 10, "service_type": "weekday"},
    ),
    # 時系列 (1 more) — last 7 days
    ChipTemplate(
        "trend-7days",
        "trend",
        "直近7日の遅延傾向",
        "Last 7 days trend",
        "time_series",
        {"time_window": "last_7_days"},
    ),
    # ランキング (1 more) — on-time worst (most untimely)
    ChipTemplate(
        "rank-ontime-worst",
        "ranking",
        "定時率が悪い路線TOP10",
        "Worst 10 by on-time rate",
        "top_n",
        {"metric": "on_time_rate", "n": 10, "best_first": False},
    ),
    # 比較 (1 more) — period vs last_30_days
    ChipTemplate(
        "cmp-period-month",
        "compare",
        "前月との比較",
        "vs previous month",
        "compare_segments",
        {"dimension": "period", "time_window": "last_30_days"},
    ),
    # 時系列 (1 more) — granularity weekly + last 30 days
    ChipTemplate(
        "trend-weekly-30",
        "trend",
        "直近30日の週別推移",
        "Weekly trend (last 30d)",
        "time_series",
        {"granularity": "week", "time_window": "last_30_days"},
    ),
]

assert len(CHIPS) == 26, f"chip catalog must have 26 entries, got {len(CHIPS)}"


CHIPS_BY_ID: dict[str, ChipTemplate] = {c.id: c for c in CHIPS}


def chips_by_category() -> dict[str, list[ChipTemplate]]:
    """Group chips by their static category, preserving CHIPS list order."""
    out: dict[str, list[ChipTemplate]] = defaultdict(list)
    for c in CHIPS:
        out[c.category].append(c)
    return dict(out)
