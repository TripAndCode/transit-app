"""The time_series answer's header names the bucket width its rows carry."""

from __future__ import annotations

import asyncio
from datetime import date

import pytest

from api.range import RangeCtx
from pipeline.query import tools

_CTX = RangeCtx(from_date=date(2026, 5, 1), to_date=date(2026, 5, 31))
_DAYS = [{"date": "2026-05-01", "avg_min": 1.5, "samples": 100, "top_offenders": []}]


@pytest.fixture
def seen_granularity(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    seen: list[str] = []

    async def fake_series(agency_id, ctx, conn, *, granularity, ch=None):
        seen.append(granularity)
        return {"days": _DAYS}

    monkeypatch.setattr(tools, "compute_trend_series", fake_series)
    return seen


@pytest.mark.parametrize(
    ("args", "locale", "summary"),
    [
        ({}, "ja", "日次トレンド (2026-05-01〜2026-05-31): 平均1.50分"),
        ({}, "en", "Daily trend (2026-05-01 to 2026-05-31): mean 1.50 min"),
        ({"granularity": "day"}, "en", "Daily trend (2026-05-01 to 2026-05-31): mean 1.50 min"),
        ({"granularity": "week"}, "ja", "週次トレンド (2026-05-01〜2026-05-31): 平均1.50分"),
        ({"granularity": "week"}, "en", "Weekly trend (2026-05-01 to 2026-05-31): mean 1.50 min"),
        ({"granularity": "month"}, "ja", "月次トレンド (2026-05-01〜2026-05-31): 平均1.50分"),
        ({"granularity": "month"}, "en", "Monthly trend (2026-05-01 to 2026-05-31): mean 1.50 min"),
    ],
)
def test_summary_names_the_bucket_width(seen_granularity, args, locale, summary):
    result = asyncio.run(tools._tool_time_series(args, _CTX, None, 1, locale))
    assert result.summary == summary
    assert seen_granularity == [args.get("granularity", "day")]


def test_an_unknown_granularity_is_titled_daily_as_its_buckets_are(seen_granularity):
    """compute_trend_series buckets any value it does not know by day."""
    result = asyncio.run(tools._tool_time_series({"granularity": "WEEK"}, _CTX, None, 1, "en"))
    assert result.summary == "Daily trend (2026-05-01 to 2026-05-31): mean 1.50 min"
