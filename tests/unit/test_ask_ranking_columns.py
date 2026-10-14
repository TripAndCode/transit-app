"""Ask's ranking tables are one row per route and carry no service column.

Each ranking row's second element names the active service filter, which the
answer's own conditions already state, so the tools leave it out of the table
rather than repeat it on every row or show a feed's raw service ids."""

from datetime import date
from decimal import Decimal

import pytest

from api.range import RangeCtx
from pipeline.query import tools


class _NoCalendarConn:
    async def fetch(self, *_args):
        return []


_CTX = RangeCtx(from_date=date(2026, 9, 14), to_date=date(2026, 9, 20))

_RANKING_ROW = ("R1", None, Decimal("3.61"), Decimal("3.20"), Decimal("6.70"), 75)
_ON_TIME_ROW = ("R1", None, Decimal("33.3"), Decimal("3.61"), 75)
_WORST_ROW = ("R1", None, 29, Decimal("3.61"), 75)


@pytest.fixture(autouse=True)
def _fake_rankings(monkeypatch):
    async def ranking(*_args, **_kwargs):
        return [_RANKING_ROW]

    async def on_time(*_args, **_kwargs):
        return [_ON_TIME_ROW]

    async def worst(*_args, **_kwargs):
        return [_WORST_ROW]

    monkeypatch.setattr(tools, "compute_ranking", ranking)
    monkeypatch.setattr(tools, "compute_on_time", on_time)
    monkeypatch.setattr(tools, "compute_worst_5min", worst)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("tool", "args", "columns", "row"),
    [
        (
            "top_n",
            {"metric": "avg_delay"},
            ["route_code", "avg_min", "p50_min", "p90_min", "samples"],
            ["R1", Decimal("3.61"), Decimal("3.20"), Decimal("6.70"), 75],
        ),
        (
            "top_n",
            {"metric": "on_time_rate"},
            ["route_code", "on_time_pct", "avg_min", "samples", "low_confidence"],
            ["R1", Decimal("33.3"), Decimal("3.61"), 75, True],
        ),
        (
            "top_n",
            {"metric": "worst_5min"},
            ["route_code", "late5_count", "avg_min", "samples"],
            ["R1", 29, Decimal("3.61"), 75],
        ),
        (
            "on_time_rate",
            {},
            ["route_code", "on_time_pct", "avg_min", "samples", "low_confidence"],
            ["R1", Decimal("33.3"), Decimal("3.61"), 75, True],
        ),
    ],
)
async def test_ranking_tools_return_no_service_column(tool, args, columns, row):
    result = await tools.dispatch(tool, args, _CTX, _NoCalendarConn(), 7)
    assert result.columns == columns
    assert result.rows == [row]
    assert all(len(r) == len(result.columns) for r in result.rows)
