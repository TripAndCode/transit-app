"""The day-type card and its modal series share one read of the calendar and
one per-date read of the aggregate, and no request aggregates static_trips."""

from datetime import date

import pytest

from api.range import RangeCtx
from pipeline.reports import overview as ov


class _CountingConn:
    def __init__(self):
        self.queries: list[str] = []

    async def fetch(self, sql, *_args):
        self.queries.append(sql)
        return []


@pytest.mark.asyncio
async def test_both_outputs_come_from_three_round_trips():
    conn = _CountingConn()
    ctx = RangeCtx(from_date=date(2026, 9, 14), to_date=date(2026, 9, 20))
    split, daily = await ov._service_day_split(7, ctx, conn)

    assert len(conn.queries) == 3
    assert not any("static_trips" in q for q in conn.queries)
    assert [e["day_type"] for e in split] == ["weekday", "saturday", "sunday_holiday"]
    assert daily == []
