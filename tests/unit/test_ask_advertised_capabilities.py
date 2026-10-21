"""What Ask advertises as answerable must be something a tool returns.

route_stats answers a single route with per-weekday average delay and sample
counts only; no Ask tool returns a share of departures more than five minutes
late. The capabilities examples, the describe_data metric list and the golden
set, whose rows Stage 2 replays verbatim, must not promise either figure.
"""

import json
from datetime import date
from pathlib import Path

import pytest

from api.range import RangeCtx
from pipeline.query.meta_tools import capabilities, describe_data

_CTX = RangeCtx(from_date=date(2026, 9, 14), to_date=date(2026, 9, 20))
_GOLDEN = Path(__file__).resolve().parents[1] / "ask_eval" / "golden_set.jsonl"


@pytest.mark.asyncio
@pytest.mark.parametrize("locale", ["ja", "en"])
async def test_single_route_examples_promise_no_percentile(locale):
    result = await capabilities({"category": "single_route"}, _CTX, None, 1, locale=locale)

    [(_, examples)] = result.pairs
    assert "p90" not in examples.lower()


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

    offending = [r["id"] for r in rows if r["expected_tool"] == "route_stats" and "p90" in r["question"].lower()]
    assert offending == []
