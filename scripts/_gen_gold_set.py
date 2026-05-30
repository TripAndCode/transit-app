"""Regenerate tests/ask_eval/gold_questions.jsonl from the chip catalog + static entries.

Run:
    poetry run python scripts/_gen_gold_set.py

The 26-chip entries are generated automatically from CHIPS; builder and
paraphrase entries are hand-authored below.  Commit both this script and
the resulting JSONL so future contributors can regenerate after catalog changes.
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

from pipeline.query.chip_catalog import CHIPS
from pipeline.query.intent import canonicalize

EVAL_CTX = {"from_date": date(2026, 5, 1), "to_date": date(2026, 5, 30)}


def chip_entries() -> list[dict]:
    out = []
    for i, c in enumerate(CHIPS, 1):
        # builder_required chips open the form UI; they can't run without a
        # route_id, so skip them for direct chip coverage.
        if c.builder_required:
            continue
        can = canonicalize(c.tool, c.args, EVAL_CTX)
        out.append(
            {
                "id": f"chip-{i:03d}",
                "ja": c.title_ja,
                "en": c.title_en,
                "expected_tool": c.tool,
                "expected_args_canonical": can,
                "via": "chip",
                "chip_id": c.id,
            }
        )
    return out


# Hand-authored builder entries — cover all 5 build-form tools with varied
# field combinations so the harness exercises broad arg-path coverage.
BUILDER_ENTRIES = [
    {
        "id": "build-001",
        "ja": "平日のワースト20を5分超で見たい",
        "en": "Weekday worst 20 by 5+ min delays",
        "expected_tool": "top_n",
        "expected_args_canonical": canonicalize(
            "top_n",
            {"metric": "worst_5min", "n": 20, "service_type": "weekday"},
            EVAL_CTX,
        ),
        "via": "builder",
    },
    {
        "id": "build-002",
        "ja": "土日祝の遅延ランキングを20件",
        "en": "Weekend top 20 delayed",
        "expected_tool": "top_n",
        "expected_args_canonical": canonicalize(
            "top_n",
            {"metric": "avg_delay", "n": 20, "service_type": "weekend"},
            EVAL_CTX,
        ),
        "via": "builder",
    },
    {
        "id": "build-003",
        "ja": "直近2週間の週別推移",
        "en": "Last 2 weeks weekly trend",
        "expected_tool": "time_series",
        "expected_args_canonical": canonicalize(
            "time_series",
            {"granularity": "week", "time_window": "last_2_weeks"},
            EVAL_CTX,
        ),
        "via": "builder",
    },
    {
        "id": "build-004",
        "ja": "直近30日の月別推移",
        "en": "Last 30 days monthly trend",
        "expected_tool": "time_series",
        "expected_args_canonical": canonicalize(
            "time_series",
            {"granularity": "month", "time_window": "last_30_days"},
            EVAL_CTX,
        ),
        "via": "builder",
    },
    {
        "id": "build-005",
        "ja": "便種別で前週との比較",
        "en": "Service type vs previous week",
        "expected_tool": "compare_segments",
        "expected_args_canonical": canonicalize(
            "compare_segments",
            {"dimension": "service_type", "time_window": "last_7_days"},
            EVAL_CTX,
        ),
        "via": "builder",
    },
    {
        "id": "build-006",
        "ja": "曜日別で前2週間との比較",
        "en": "DOW vs previous 2 weeks",
        "expected_tool": "compare_segments",
        "expected_args_canonical": canonicalize(
            "compare_segments",
            {"dimension": "dow", "time_window": "last_2_weeks"},
            EVAL_CTX,
        ),
        "via": "builder",
    },
    {
        "id": "build-007",
        "ja": "路線16071の遅延統計",
        "en": "Route 16071 delay statistics",
        "expected_tool": "route_stats",
        "expected_args_canonical": canonicalize(
            "route_stats",
            {"route_id": "16071"},
            EVAL_CTX,
        ),
        "via": "builder",
    },
    {
        "id": "build-008",
        "ja": "路線22171の運行情報",
        "en": "Route 22171 metadata",
        "expected_tool": "route_meta",
        "expected_args_canonical": canonicalize(
            "route_meta",
            {"route_id": "22171"},
            EVAL_CTX,
        ),
        "via": "builder",
    },
    {
        "id": "build-009",
        "ja": "路線16071の日別推移",
        "en": "Route 16071 daily trend",
        "expected_tool": "time_series",
        "expected_args_canonical": canonicalize(
            "time_series",
            {"route_id": "16071", "granularity": "day"},
            EVAL_CTX,
        ),
        "via": "builder",
    },
    {
        "id": "build-010",
        "ja": "停留所一覧の次のページ",
        "en": "Next page of stops",
        "expected_tool": "describe_data",
        "expected_args_canonical": canonicalize(
            "describe_data",
            {"kind": "stops", "offset": 50},
            EVAL_CTX,
        ),
        "via": "builder",
    },
    {
        "id": "build-011",
        "ja": "停留所一覧の3ページ目",
        "en": "Stops page 3",
        "expected_tool": "describe_data",
        "expected_args_canonical": canonicalize(
            "describe_data",
            {"kind": "stops", "offset": 100},
            EVAL_CTX,
        ),
        "via": "builder",
    },
    {
        "id": "build-012",
        "ja": "降順の停留所一覧",
        "en": "Stops list descending",
        "expected_tool": "describe_data",
        "expected_args_canonical": canonicalize(
            "describe_data",
            {"kind": "stops", "order": "desc"},
            EVAL_CTX,
        ),
        "via": "builder",
    },
    {
        "id": "build-013",
        "ja": "路線一覧を100件表示",
        "en": "Show 100 routes",
        "expected_tool": "describe_data",
        "expected_args_canonical": canonicalize(
            "describe_data",
            {"kind": "routes", "limit": 100},
            EVAL_CTX,
        ),
        "via": "builder",
    },
    {
        "id": "build-014",
        "ja": "平日の定時率トップ5",
        "en": "Weekday top 5 by on-time rate",
        "expected_tool": "top_n",
        "expected_args_canonical": canonicalize(
            "top_n",
            {"metric": "on_time_rate", "n": 5, "best_first": True, "service_type": "weekday"},
            EVAL_CTX,
        ),
        "via": "builder",
    },
]

# Hand-authored paraphrase-reachable entries — natural questions a real user
# might type that should resolve to a known chip or builder path.
# These are informational (not a CI gate) but document expected reachability.
PARAPHRASE_ENTRIES = [
    {
        "id": "paraphrase-001",
        "ja": "遅延の多い路線を上から10件教えて",
        "en": "Show top 10 most-delayed routes",
        "expected_tool": "top_n",
        "expected_args_canonical": canonicalize("top_n", {"metric": "avg_delay", "n": 10}, EVAL_CTX),
        "via": "paraphrase-reachable",
        "reachable_via_chip": "rank-delay-top",
    },
    {
        "id": "paraphrase-002",
        "ja": "定時率が高い順に教えて",
        "en": "Sort by on-time rate descending",
        "expected_tool": "top_n",
        "expected_args_canonical": canonicalize(
            "top_n", {"metric": "on_time_rate", "n": 10, "best_first": True}, EVAL_CTX
        ),
        "via": "paraphrase-reachable",
        "reachable_via_chip": "rank-ontime-top",
    },
    {
        "id": "paraphrase-003",
        "ja": "停留所はいくつあるの？",
        "en": "How many stops are there?",
        "expected_tool": "describe_data",
        "expected_args_canonical": canonicalize("describe_data", {"kind": "stops"}, EVAL_CTX),
        "via": "paraphrase-reachable",
        "reachable_via_chip": "meta-stops",
    },
    {
        "id": "paraphrase-004",
        "ja": "データの期間を教えて",
        "en": "What's the date range of the data?",
        "expected_tool": "describe_data",
        "expected_args_canonical": canonicalize("describe_data", {"kind": "date_range"}, EVAL_CTX),
        "via": "paraphrase-reachable",
        "reachable_via_chip": "meta-date-range",
    },
    {
        "id": "paraphrase-005",
        "ja": "平日と週末の遅延の違いは？",
        "en": "How do weekday vs weekend delays differ?",
        "expected_tool": "compare_segments",
        "expected_args_canonical": canonicalize("compare_segments", {"dimension": "dow"}, EVAL_CTX),
        "via": "paraphrase-reachable",
        "reachable_via_chip": "cmp-dow",
    },
    {
        "id": "paraphrase-006",
        "ja": "直近2週間の遅延の様子",
        "en": "Last two weeks delay overview",
        "expected_tool": "time_series",
        "expected_args_canonical": canonicalize("time_series", {"time_window": "last_2_weeks"}, EVAL_CTX),
        "via": "paraphrase-reachable",
        "reachable_via_chip": "trend-2weeks",
    },
    {
        "id": "paraphrase-007",
        "ja": "今月どんな路線があるか教えて",
        "en": "What routes do you have for this month?",
        "expected_tool": "describe_data",
        "expected_args_canonical": canonicalize("describe_data", {"kind": "routes"}, EVAL_CTX),
        "via": "paraphrase-reachable",
        "reachable_via_chip": "meta-routes",
    },
    {
        "id": "paraphrase-008",
        "ja": "週末の遅延ランキングを見せて",
        "en": "Show the weekend delay ranking",
        "expected_tool": "top_n",
        "expected_args_canonical": canonicalize(
            "top_n", {"metric": "avg_delay", "n": 10, "service_type": "weekend"}, EVAL_CTX
        ),
        "via": "paraphrase-reachable",
        "reachable_via_chip": "rank-weekend",
    },
    {
        "id": "paraphrase-009",
        "ja": "5分以上遅れる便が多い路線",
        "en": "Routes with frequent 5+ minute delays",
        "expected_tool": "top_n",
        "expected_args_canonical": canonicalize("top_n", {"metric": "worst_5min", "n": 10}, EVAL_CTX),
        "via": "paraphrase-reachable",
        "reachable_via_chip": "rank-5min-top",
    },
    {
        "id": "paraphrase-010",
        "ja": "日別の遅延の推移",
        "en": "Daily delay trend",
        "expected_tool": "time_series",
        "expected_args_canonical": canonicalize("time_series", {"granularity": "day"}, EVAL_CTX),
        "via": "paraphrase-reachable",
        "reachable_via_chip": "trend-daily",
    },
]


def main() -> None:
    entries = chip_entries() + BUILDER_ENTRIES + PARAPHRASE_ENTRIES
    out_path = Path("tests/ask_eval/gold_questions.jsonl")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w", encoding="utf-8") as f:
        for e in entries:
            f.write(json.dumps(e, ensure_ascii=False) + "\n")
    print(f"wrote {len(entries)} entries to {out_path}")


if __name__ == "__main__":
    main()
