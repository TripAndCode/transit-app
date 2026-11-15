"""api.retention_guard: a live read of `updates` refuses a range before the
agency's retention floor, at a cost of at most one floor read per agency per
cache window."""

from __future__ import annotations

import json
import types
from datetime import date

import pytest

from api import retention_guard
from api.retention_guard import (
    FLOOR_CACHE_TTL_SECONDS,
    RANGE_NOT_RETAINED_CODE,
    RangeNotRetained,
    range_not_retained_handler,
    require_retained,
)
from pipeline import live_filing
from pipeline.cache import clear_all
from pipeline.clickhouse import LIVE_TABLE, UPDATES_TABLE

FLOOR = date(2025, 3, 1)


class _Reads:
    def __init__(self, floors: dict[int, date]) -> None:
        self.floors = floors
        self.calls: list[int] = []

    def __call__(self, agency_id: int) -> date | None:
        self.calls.append(agency_id)
        return self.floors.get(agency_id)


@pytest.fixture
def reads(monkeypatch):
    retention_guard.reset_floor_cache()
    fake = _Reads({1: FLOOR})
    monkeypatch.setattr(retention_guard, "_read_floor", fake)
    yield fake
    retention_guard.reset_floor_cache()


@pytest.fixture
def clock(monkeypatch):
    now = types.SimpleNamespace(t=1000.0)
    monkeypatch.setattr(retention_guard.time, "monotonic", lambda: now.t)
    return now


async def test_a_read_starting_before_the_floor_is_refused_with_the_floor(reads):
    with pytest.raises(RangeNotRetained) as refused:
        await require_retained(1, date(2025, 2, 28))
    assert refused.value.floor == FLOOR


async def test_a_read_starting_at_the_floor_passes(reads):
    await require_retained(1, FLOOR)


async def test_without_a_floor_any_read_passes(reads):
    await require_retained(2, date(2000, 1, 1))


async def test_a_floor_is_read_once_per_agency_per_cache_window(reads, clock):
    for _ in range(5):
        await require_retained(1, FLOOR)
        await require_retained(2, FLOOR)
    assert reads.calls == [1, 2]
    clock.t += FLOOR_CACHE_TTL_SECONDS
    await require_retained(2, FLOOR)
    assert reads.calls == [1, 2, 2]


async def test_the_compute_cache_clear_forgets_the_floors(reads):
    await require_retained(2, FLOOR)
    clear_all()
    await require_retained(2, FLOOR)
    assert reads.calls == [2, 2]


async def test_filing_refuses_before_it_reads_anything(reads):
    """conn and ch are None: any read before the guard would fail otherwise."""
    with pytest.raises(RangeNotRetained):
        await live_filing.filing(None, None, 1, date(2025, 2, 28), FLOOR, table=UPDATES_TABLE)


async def test_filing_of_the_live_table_reads_no_floor(reads, monkeypatch):
    async def not_stamping(conn, agency_id):
        return False

    monkeypatch.setattr(live_filing, "_stamps_versions", not_stamping)
    filed = await live_filing.filing(None, None, 1, date(2025, 2, 28), FLOOR, table=LIVE_TABLE)
    assert filed.between == (date(2025, 2, 28), FLOOR)
    assert reads.calls == []


def _request(locale: str):
    return types.SimpleNamespace(state=types.SimpleNamespace(locale=locale))


async def test_the_refusal_answers_422_naming_the_floor():
    resp = await range_not_retained_handler(_request("en"), RangeNotRetained(FLOOR))
    assert resp.status_code == 422
    assert json.loads(bytes(resp.body)) == {
        "detail": "Raw readings before 2025-03-01 are no longer retained, so this view can't cover that period. "
        "Choose a period from 2025-03-01 on; the aggregated reports still cover the earlier dates.",
        "code": RANGE_NOT_RETAINED_CODE,
        "floor": "2025-03-01",
    }


async def test_the_refusal_is_localized():
    resp = await range_not_retained_handler(_request("ja"), RangeNotRetained(FLOOR))
    assert json.loads(bytes(resp.body))["detail"] == (
        "2025-03-01 より前の生の観測データは保存期間を過ぎたため、この条件では集計できません。"
        "2025-03-01 以降の期間を選んでください。それより前の期間は集計済みのレポートで確認できます。"
    )


def test_the_app_routes_the_refusal_through_the_handler():
    from api.main import app

    assert app.exception_handlers[RangeNotRetained] is range_not_retained_handler
