"""async_lru_cache emits hit/miss to pipeline.perf and supports clear_all()."""

import asyncio

import pytest

from pipeline import cache, perf


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


def test_leader_cancellation_does_not_poison_waiters():
    """CancelGETOnDisconnectMiddleware can cancel the in-flight leader; healthy
    waiters must re-elect a new leader and succeed, not inherit CancelledError."""
    calls = {"n": 0}

    @cache.async_lru_cache(maxsize=4, ttl_seconds=60)
    async def fn(x):
        calls["n"] += 1
        await asyncio.sleep(0.05)
        return x * 2

    async def run():
        leader = asyncio.create_task(fn(1))
        await asyncio.sleep(0.01)  # let leader become inflight
        w1 = asyncio.create_task(fn(1))
        w2 = asyncio.create_task(fn(1))
        await asyncio.sleep(0.01)  # waiters joined
        leader.cancel()
        results = await asyncio.gather(leader, w1, w2, return_exceptions=True)
        assert isinstance(results[0], asyncio.CancelledError)
        assert results[1] == 2
        assert results[2] == 2

    asyncio.run(run())
    assert calls["n"] == 2  # original leader + one re-elected leader


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


def test_keyable_rejects_unhashable_args_instead_of_collapsing_them():
    """_keyable() must fail loud on an unhashable arg (list, dict) rather
    than collapse it to a token keyed only on its class name: that would
    discard the actual value, so two requests with different unhashable
    values of the same type would silently collide on one cache entry.
    Failing forces a future caller to add explicit key handling instead."""
    with pytest.raises(TypeError, match="not hashable"):
        cache._keyable(["a", "list", "is", "unhashable"])
    with pytest.raises(TypeError, match="not hashable"):
        cache._keyable({"a": "dict too"})


def test_keyable_still_handles_connection_like_and_plain_hashable_args():
    class Connection:
        pass

    assert cache._keyable(Connection()) == "<conn:Connection>"
    assert cache._keyable(42) == 42
    assert cache._keyable("s") == "s"
    assert cache._keyable((1, 2)) == (1, 2)


def test_compute_in_flight_across_clear_is_not_cached():
    """A compute that read its inputs before cache_clear() must not store that
    pre-clear value, or the next caller is served data the clear invalidated."""
    calls = {"n": 0}
    source = {"value": "before"}

    async def run():
        started = asyncio.Event()
        release = asyncio.Event()

        @cache.async_lru_cache(maxsize=4, ttl_seconds=60)
        async def fn(x):
            calls["n"] += 1
            seen = source["value"]
            started.set()
            await release.wait()
            return seen

        leader = asyncio.create_task(fn(1))
        await asyncio.wait_for(started.wait(), 1)
        source["value"] = "after"
        fn.cache_clear()
        release.set()
        assert await asyncio.wait_for(leader, 1) == "before"
        assert await asyncio.wait_for(fn(1), 1) == "after"

    asyncio.run(run())
    assert calls["n"] == 2


def test_leader_from_before_clear_leaves_newer_leader_inflight():
    """A leader that finishes after cache_clear() must not remove the in-flight
    entry of a leader started after the clear, so later callers still join it."""
    calls = {"n": 0}

    async def run():
        entered = [asyncio.Event() for _ in range(3)]
        gates = [asyncio.Event() for _ in range(3)]

        @cache.async_lru_cache(maxsize=4, ttl_seconds=60)
        async def fn(x):
            i = calls["n"]
            calls["n"] += 1
            entered[i].set()
            await gates[i].wait()
            return f"compute-{i}"

        a = asyncio.create_task(fn(1))
        await asyncio.wait_for(entered[0].wait(), 1)
        fn.cache_clear()
        b = asyncio.create_task(fn(1))
        await asyncio.wait_for(entered[1].wait(), 1)
        gates[0].set()
        assert await asyncio.wait_for(a, 1) == "compute-0"

        c = asyncio.create_task(fn(1))
        for _ in range(3):
            await asyncio.sleep(0)
        assert calls["n"] == 2
        gates[1].set()
        gates[2].set()
        assert await asyncio.wait_for(b, 1) == "compute-1"
        assert await asyncio.wait_for(c, 1) == "compute-1"

    asyncio.run(run())
