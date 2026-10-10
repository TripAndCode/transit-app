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


def _stage(value, name=None, started=None):
    async def fake(*_args, **_kwargs):
        if started is not None:
            started.append(name)
        await _yield()
        return value

    return fake


@pytest.fixture
def stages(monkeypatch):
    """The order the gathered stages start in, as the fakes record it."""
    started: list[str] = []
    gathered = {
        "_headline_stats": (2.5, 40),
        "_movers": [],
        "_concentration": None,
        "_top_delayed_routes": [],
        "_peak_hour": None,
        "_peak_hour_by_dow": None,
        "_service_split": None,
        "_service_split_daily": [],
        "_daily_sparkline": [],
    }
    monkeypatch.setattr(ov, "_latest_data_date", _stage(date(2026, 5, 24)))
    for name, value in gathered.items():
        monkeypatch.setattr(ov, name, _stage(value, name, started))
    return started


@pytest.mark.asyncio
async def test_fanout_never_holds_more_than_the_limit(stages):
    pool = _FakePool()
    ctx = RangeCtx(from_date=date(2026, 5, 11), to_date=date(2026, 5, 24))
    # No request connection: the pool path takes every connection it uses.
    payload = await ov.compute_overview_summary(7, ctx, None, "ja", pool=pool)

    # The latest-date read ahead of the fan-out takes one, and every stage its own.
    assert pool.acquires == 12
    assert pool.peak <= ov.OVERVIEW_FANOUT_LIMIT
    assert pool.peak == ov.OVERVIEW_FANOUT_LIMIT, "the bound is a ceiling, not a serialiser"
    assert pool.active == 0
    assert payload["headline"]["avg_min"] == 2.5


@pytest.mark.asyncio
async def test_the_peak_hour_pair_takes_the_first_slots(stages):
    """The two `_peak_hour_by_dow` reads dominate a cold load, so under the
    bound they must not queue behind stages that finish quickly."""
    ctx = RangeCtx(from_date=date(2026, 5, 11), to_date=date(2026, 5, 24))
    await ov.compute_overview_summary(7, ctx, None, "ja", pool=_FakePool())

    assert stages[:2] == ["_peak_hour_by_dow", "_peak_hour_by_dow"]
