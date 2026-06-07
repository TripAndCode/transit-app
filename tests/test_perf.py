# tests/test_perf.py
"""Unit tests for the in-process perf registry (pipeline/perf.py)."""

import asyncio
import logging

import pytest

from pipeline import perf


# Override the session-scoped DB fixture — pure-Python tests, no DB needed.
@pytest.fixture(scope="session", autouse=True)
def apply_schema():
    yield


@pytest.fixture(autouse=True)
def _clean_registry():
    perf.reset()
    yield
    perf.reset()


def test_record_and_snapshot():
    perf.record("op.a", 10.0)
    perf.record("op.a", 30.0)
    snap = perf.snapshot()
    st = snap["ops"]["op.a"]
    assert st["count"] == 2
    assert st["avg_ms"] == 20.0
    assert st["max_ms"] == 30.0
    assert st["p50_ms"] == 10.0  # nearest-rank on [10, 30]
    assert st["p95_ms"] == 30.0


def test_ring_buffer_bounded():
    for i in range(600):
        perf.record("op.ring", float(i))
    snap = perf.snapshot()
    st = snap["ops"]["op.ring"]
    assert st["count"] == 600          # lifetime count keeps counting
    assert st["p50_ms"] >= 100.0       # percentiles from last 500 only (100..599)


def test_reset_clears_everything():
    perf.record("op.x", 1.0)
    perf.record_cache("fn_y", hit=True)
    perf.reset()
    snap = perf.snapshot()
    assert snap["ops"] == {}
    assert snap["caches"] == {}


def test_record_cache_hit_rate():
    perf.record_cache("compute_z", hit=True)
    perf.record_cache("compute_z", hit=True)
    perf.record_cache("compute_z", hit=False)
    snap = perf.snapshot()
    c = snap["caches"]["compute_z"]
    assert c["hits"] == 2
    assert c["misses"] == 1
    assert c["hit_rate"] == 0.667


def test_timed_decorator_records():
    @perf.timed("op.deco")
    async def fn(x):
        return x + 1

    result = asyncio.run(fn(1))
    assert result == 2
    assert perf.snapshot()["ops"]["op.deco"]["count"] == 1


def test_timed_records_on_exception():
    @perf.timed("op.boom")
    async def fn():
        raise ValueError("boom")

    with pytest.raises(ValueError):
        asyncio.run(fn())
    assert perf.snapshot()["ops"]["op.boom"]["count"] == 1


def test_timed_block_records():
    async def run():
        async with perf.timed_block("op.block"):
            await asyncio.sleep(0)

    asyncio.run(run())
    assert perf.snapshot()["ops"]["op.block"]["count"] == 1


def test_slow_warning_logged(monkeypatch, caplog):
    monkeypatch.setenv("PERF_SLOW_MS", "5")
    with caplog.at_level(logging.WARNING, logger="pipeline.perf"):
        perf.record("op.slow", 10.0)
    assert any("op.slow" in r.message for r in caplog.records)


def test_no_warning_under_threshold(monkeypatch, caplog):
    monkeypatch.setenv("PERF_SLOW_MS", "500")
    with caplog.at_level(logging.WARNING, logger="pipeline.perf"):
        perf.record("op.fast", 10.0)
    assert not caplog.records
