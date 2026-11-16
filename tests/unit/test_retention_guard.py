"""pipeline.live_filing.require_retained: a live read of `updates` refuses a
range before the agency's retention floor, read fresh on the read's own
connection, and api.range_not_retained answers the refusal."""

from __future__ import annotations

import json
import types
from datetime import date

import pytest

from api.range_not_retained import range_not_retained_handler
from pipeline import live_filing
from pipeline.clickhouse import LIVE_TABLE, UPDATES_TABLE
from pipeline.live_filing import require_retained
from pipeline.retained_reads import RANGE_NOT_RETAINED_CODE, RangeNotRetained

FLOOR = date(2025, 3, 1)


class _Floors:
    """Stands in for pipeline.retention.retention_floor_async, counting reads."""

    def __init__(self, floors: dict[int, date]) -> None:
        self.floors = floors
        self.reads: list[tuple[object, int]] = []

    async def __call__(self, conn, agency_id: int) -> date | None:
        self.reads.append((conn, agency_id))
        return self.floors.get(agency_id)


@pytest.fixture
def floors(monkeypatch):
    fake = _Floors({1: FLOOR})
    monkeypatch.setattr(live_filing.retention, "retention_floor_async", fake)
    return fake


async def test_a_read_starting_before_the_floor_is_refused_with_the_floor(floors):
    with pytest.raises(RangeNotRetained) as refused:
        await require_retained(object(), 1, date(2025, 2, 28))
    assert refused.value.floor == FLOOR


async def test_a_read_starting_at_the_floor_passes(floors):
    await require_retained(object(), 1, FLOOR)


async def test_without_a_floor_any_read_passes(floors):
    await require_retained(object(), 2, date(2000, 1, 1))


async def test_the_floor_is_read_fresh_on_the_reads_own_connection(floors):
    conn = object()
    await require_retained(conn, 2, FLOOR)
    floors.floors[2] = FLOOR
    with pytest.raises(RangeNotRetained):
        await require_retained(conn, 2, date(2025, 2, 28))
    assert floors.reads == [(conn, 2), (conn, 2)]


async def test_filing_refuses_before_it_reads_anything_else(floors):
    """ch is None: any ClickHouse read before the guard would fail otherwise."""
    with pytest.raises(RangeNotRetained):
        await live_filing.filing(object(), None, 1, date(2025, 2, 28), FLOOR, table=UPDATES_TABLE)


async def test_filing_of_the_live_table_reads_no_floor(floors, monkeypatch):
    async def not_stamping(conn, agency_id):
        return False

    monkeypatch.setattr(live_filing, "_stamps_versions", not_stamping)
    filed = await live_filing.filing(object(), None, 1, date(2025, 2, 28), FLOOR, table=LIVE_TABLE)
    assert filed.between == (date(2025, 2, 28), FLOOR)
    assert floors.reads == []


def _request(locale: str):
    return types.SimpleNamespace(state=types.SimpleNamespace(locale=locale))


async def test_the_refusal_answers_422_naming_the_floor():
    resp = await range_not_retained_handler(_request("en"), RangeNotRetained(FLOOR))
    assert resp.status_code == 422
    assert json.loads(bytes(resp.body)) == {
        "detail": "Detailed readings before 2025-03-01 are no longer kept, so this view can't cover that period. "
        "Choose a period from 2025-03-01 on; the reports built from daily totals still cover the earlier dates.",
        "code": RANGE_NOT_RETAINED_CODE,
        "floor": "2025-03-01",
    }


async def test_the_refusal_is_localized():
    resp = await range_not_retained_handler(_request("ja"), RangeNotRetained(FLOOR))
    assert json.loads(bytes(resp.body))["detail"] == (
        "2025-03-01 より前の詳細な観測データは保存期間を過ぎているため、この表示では扱えません。"
        "2025-03-01 以降の期間を選んでください。それより前の期間は日ごとの集計によるレポートで確認できます。"
    )


def test_the_app_routes_the_refusal_through_the_handler():
    from api.main import app

    assert app.exception_handlers[RangeNotRetained] is range_not_retained_handler
