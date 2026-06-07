"""async_lru_cache emits hit/miss to pipeline.perf and supports clear_all()."""

import asyncio

import pytest

from pipeline import cache, perf


# Override the session-scoped DB fixture — pure-Python tests, no DB needed.
@pytest.fixture(scope="session", autouse=True)
def apply_schema():
    yield


@pytest.fixture(autouse=True)
def _clean():
    perf.reset()
    yield
    perf.reset()


def test_cache_records_hit_and_miss():
    calls = {"n": 0}

    @cache.async_lru_cache(maxsize=4, ttl_seconds=60)
    async def fn(x):
        calls["n"] += 1
        return x * 2

    async def run():
        await fn(1)  # miss
        await fn(1)  # hit
        await fn(2)  # miss

    asyncio.run(run())
    assert calls["n"] == 2
    c = perf.snapshot()["caches"]["fn"]
    assert c["misses"] == 2
    assert c["hits"] == 1


def test_clear_all_empties_every_registered_cache():
    calls = {"n": 0}

    @cache.async_lru_cache(maxsize=4, ttl_seconds=60)
    async def fn(x):
        calls["n"] += 1
        return x

    async def run():
        await fn(1)
        cache.clear_all()
        await fn(1)  # must recompute after clear_all

    asyncio.run(run())
    assert calls["n"] == 2


def test_concurrent_misses_coalesce_to_single_compute():
    calls = {"n": 0}

    @cache.async_lru_cache(maxsize=4, ttl_seconds=60)
    async def fn(x):
        calls["n"] += 1
        await asyncio.sleep(0.05)
        return x * 2

    async def run():
        results = await asyncio.gather(*(fn(1) for _ in range(5)))
        assert results == [2] * 5

    asyncio.run(run())
    assert calls["n"] == 1
    c = perf.snapshot()["caches"]["fn"]
    assert c["misses"] == 1
    assert c["hits"] == 4


def test_inflight_exception_propagates_to_all_waiters_and_not_cached():
    calls = {"n": 0}

    @cache.async_lru_cache(maxsize=4, ttl_seconds=60)
    async def fn(x):
        calls["n"] += 1
        await asyncio.sleep(0.02)
        if calls["n"] == 1:
            raise ValueError("boom")
        return x

    async def run():
        results = await asyncio.gather(*(fn(1) for _ in range(3)), return_exceptions=True)
        assert all(isinstance(r, ValueError) for r in results)
        assert await fn(1) == 1  # next call recomputes — exception not cached

    asyncio.run(run())
    assert calls["n"] == 2
