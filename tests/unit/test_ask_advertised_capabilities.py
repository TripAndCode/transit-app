"""What Ask advertises as answerable must be something a tool returns.

route_stats answers a single route with per-weekday average delay and sample
counts only; no Ask tool returns a share of departures more than five minutes
late. The capabilities examples, the describe_data metric list and the golden
set, whose rows Stage 2 replays verbatim, must not promise either figure.
"""

import json
import re
import unicodedata
from datetime import date
from pathlib import Path

import pytest

from api.range import RangeCtx
from pipeline.query.meta_tools import capabilities, describe_data

_CTX = RangeCtx(from_date=date(2026, 9, 14), to_date=date(2026, 9, 20))
_GOLDEN = Path(__file__).resolve().parents[1] / "ask_eval" / "golden_set.jsonl"
# "top10" must not read as p10, hence the lookbehind.
_PERCENTILE = re.compile(r"(?<![a-z])p\d{2}|percentile|パーセンタイル|分位")


def _asks_for_a_percentile(text: str) -> bool:
    return _PERCENTILE.search(unicodedata.normalize("NFKC", text).lower()) is not None


@pytest.mark.parametrize(
    "text",
    ["路線16071のp90", "22171のＰ９０", "90パーセンタイル遅延", "90th percentile delay", "90分位点の遅延"],
)
def test_the_percentile_guard_catches_each_phrasing(text):
    assert _asks_for_a_percentile(text)


def test_the_percentile_guard_ignores_a_top_n_count():
    assert not _asks_for_a_percentile("定時率TOP10を教えて")


@pytest.mark.asyncio
@pytest.mark.parametrize("locale", ["ja", "en"])
async def test_single_route_examples_promise_no_percentile(locale):
    result = await capabilities({"category": "single_route"}, _CTX, None, 1, locale=locale)

    [(_, examples)] = result.pairs
    assert not _asks_for_a_percentile(examples)


@pytest.mark.asyncio
async def test_metric_list_names_no_late5_share_in_either_locale():
    keys = {}
    for locale in ("ja", "en"):
        result = await describe_data({"kind": "metrics"}, _CTX, None, 1, locale=locale)
        keys[locale] = [k for k, _ in result.pairs]

    assert keys["ja"] == keys["en"]
    assert "late5_pct" not in keys["ja"]


def test_no_golden_row_routes_a_percentile_question_to_route_stats():
    with _GOLDEN.open() as f:
        rows = [json.loads(line) for line in f if line.strip()]

    offending = [r["id"] for r in rows if r["expected_tool"] == "route_stats" and _asks_for_a_percentile(r["question"])]
    assert offending == []
