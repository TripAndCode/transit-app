"""The overview's pool-gather path may hold at most OVERVIEW_FANOUT_LIMIT
connections at once, however many stages it dispatches. Driven against a fake
pool that counts concurrent acquires; every stage helper is replaced by a
fake that yields once so the gather actually interleaves."""

from datetime import date

import pytest

from api.range import RangeCtx
from pipeline.reports import overview as ov


class _Acquire:
    def __init__(self, pool):
        self._pool = pool

    async def __aenter__(self):
        self._pool.active += 1
        self._pool.peak = max(self._pool.peak, self._pool.active)
        self._pool.acquires += 1
        await _yield()
        return object()

    async def __aexit__(self, *exc):
        await _yield()
        self._pool.active -= 1
        return False


class _FakePool:
    def __init__(self):
        self.active = 0
        self.peak = 0
        self.acquires = 0

    def acquire(self):
        return _Acquire(self)


async def _yield():
    import asyncio

    await asyncio.sleep(0)


def _stage(value):
    async def fake(*_args, **_kwargs):
        await _yield()
        return value

    return fake


@pytest.fixture
def stages(monkeypatch):
    monkeypatch.setattr(ov, "_latest_data_date", _stage(date(2026, 5, 24)))
    monkeypatch.setattr(ov, "_headline_stats", _stage((2.5, 40)))
    monkeypatch.setattr(ov, "_movers", _stage([]))
    monkeypatch.setattr(ov, "_concentration", _stage(None))
    monkeypatch.setattr(ov, "_top_delayed_routes", _stage([]))
    monkeypatch.setattr(ov, "_peak_hour", _stage(None))
    monkeypatch.setattr(ov, "_peak_hour_by_dow", _stage(None))
    monkeypatch.setattr(ov, "_service_split", _stage(None))
    monkeypatch.setattr(ov, "_service_split_daily", _stage([]))
    monkeypatch.setattr(ov, "_daily_sparkline", _stage([]))


@pytest.mark.asyncio
async def test_fanout_never_holds_more_than_the_limit(stages):
    pool = _FakePool()
    ctx = RangeCtx(from_date=date(2026, 5, 11), to_date=date(2026, 5, 24))
    payload = await ov.compute_overview_summary(7, ctx, object(), "ja", pool=pool)

    assert pool.acquires == 11, "every stage still gets its own connection"
    assert pool.peak <= ov.OVERVIEW_FANOUT_LIMIT
    assert pool.peak == ov.OVERVIEW_FANOUT_LIMIT, "the bound is a ceiling, not a serialiser"
    assert pool.active == 0
    assert payload["headline"]["avg_min"] == 2.5


def test_the_limit_leaves_headroom_in_the_pool():
    """api.main sizes the pool at 20 with the fan-out in mind; the bound must
    stay well inside it so concurrent overview requests do not starve others."""
    assert ov.OVERVIEW_FANOUT_LIMIT == 4
