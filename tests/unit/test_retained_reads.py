"""A live read whose floor check passed is checked again once its computation's
last scan has ended (pipeline.retained_reads), so an answer missing rows a
prune dropped mid-read is neither returned nor cached: in a cached compute
(pipeline.cache), an Ask tool, a request (api.range_not_retained) and before
a snapshot is stored."""

from __future__ import annotations

import asyncio
from datetime import date

import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

from api.range_not_retained import range_not_retained_handler, recheck_live_reads
from pipeline import retained_reads, retention
from pipeline.cache import async_lru_cache
from pipeline.live_filing import require_retained
from pipeline.retained_reads import RangeNotRetained

START = date(2025, 2, 10)
FLOOR = date(2025, 3, 1)


class Connection:
    """Stands in for an asyncpg connection; the class name is what
    pipeline.cache recognises a connection argument by."""


class _Floors:
    """Stands in for pipeline.retention.retention_floor_async, counting reads."""

    def __init__(self) -> None:
        self.floors: dict[int, date] = {}
        self.reads: list[tuple[object, int]] = []

    async def __call__(self, conn, agency_id: int) -> date | None:
        self.reads.append((conn, agency_id))
        return self.floors.get(agency_id)


@pytest.fixture
def floors(monkeypatch):
    fake = _Floors()
    monkeypatch.setattr(retention, "retention_floor_async", fake)
    return fake


async def test_a_compute_whose_floor_rises_before_it_returns_is_refused_and_caches_nothing(floors):
    calls = []

    @async_lru_cache(ttl_seconds=600)
    async def compute(conn, agency_id: int) -> str:
        calls.append(agency_id)
        await require_retained(conn, agency_id, START)
        floors.floors[agency_id] = FLOOR  # a prune commits its floor mid-scan
        return f"partial {len(calls)}"

    with pytest.raises(RangeNotRetained) as refused:
        await compute(Connection(), 1)
    assert refused.value.floor == FLOOR
    # The second run's own check refused the requested period.
    assert len(calls) == 2


async def test_a_refused_compute_leaves_nothing_cached_for_the_next_caller(floors):
    runs = []

    @async_lru_cache(ttl_seconds=600)
    async def compute(conn, agency_id: int) -> str:
        runs.append(agency_id)
        await require_retained(conn, agency_id, START)
        if len(runs) == 1:
            floors.floors[agency_id] = FLOOR
        return f"run {len(runs)}"

    with pytest.raises(RangeNotRetained):
        await compute(Connection(), 1)
    floors.floors.clear()
    assert await compute(Connection(), 1) == "run 3"
    assert await compute(Connection(), 1) == "run 3"


async def test_a_derived_span_the_floor_passes_mid_compute_is_computed_again_from_the_floor(floors):
    """A look-back starts no earlier than the floor it reads, so the second
    run starts it at the new floor instead of refusing the request."""
    starts = []

    @async_lru_cache(ttl_seconds=600)
    async def compute(conn, agency_id: int) -> date:
        floor = await retention.retention_floor_async(conn, agency_id)
        first = START if floor is None else max(START, floor)
        await require_retained(conn, agency_id, first)
        starts.append(first)
        floors.floors.setdefault(agency_id, FLOOR)
        return first

    assert await compute(Connection(), 1) == FLOOR
    assert starts == [START, FLOOR]
    assert await compute(Connection(), 1) == FLOOR
    assert starts == [START, FLOOR]


async def test_a_coalesced_waiter_gets_the_refusal_not_the_partial_answer(floors):
    release = asyncio.Event()

    @async_lru_cache(ttl_seconds=600)
    async def compute(conn, agency_id: int) -> str:
        await require_retained(conn, agency_id, START)
        await release.wait()
        floors.floors[agency_id] = FLOOR
        return "partial"

    leader = asyncio.create_task(compute(Connection(), 1))
    await asyncio.sleep(0)
    waiter = asyncio.create_task(compute(Connection(), 1))
    await asyncio.sleep(0)
    release.set()
    results = await asyncio.gather(leader, waiter, return_exceptions=True)
    assert all(isinstance(r, RangeNotRetained) for r in results), results


async def test_without_a_floor_a_compute_adds_one_floor_read_on_its_own_connection(floors):
    conn = Connection()

    @async_lru_cache(ttl_seconds=600)
    async def compute(conn, agency_id: int) -> str:
        await require_retained(conn, agency_id, START)
        await require_retained(conn, agency_id, date(2025, 2, 1))
        return "whole"

    assert await compute(conn, 1) == "whole"
    assert floors.reads == [(conn, 1)] * 3
    assert await compute(conn, 1) == "whole"
    assert len(floors.reads) == 3


async def test_a_compute_without_live_reads_reads_no_floor(floors):
    @async_lru_cache(ttl_seconds=600)
    async def compute(conn, agency_id: int) -> str:
        return "aggregates"

    assert await compute(Connection(), 1) == "aggregates"
    assert floors.reads == []


async def test_the_recheck_runs_on_the_computes_own_connection_not_a_stage_s(floors):
    """A stage's own connection is back in the pool once the stage ends."""
    own, stage = Connection(), Connection()

    @async_lru_cache(ttl_seconds=600)
    async def compute(conn, agency_id: int) -> str:
        await require_retained(stage, agency_id, START)
        return "whole"

    await compute(own, 1)
    assert floors.reads == [(stage, 1), (own, 1)]


async def test_a_nested_cached_call_keeps_its_own_record_and_the_outer_keeps_its(floors):
    conn = Connection()

    @async_lru_cache(ttl_seconds=600)
    async def inner(conn, agency_id: int) -> str:
        await require_retained(conn, agency_id, START)
        return "inner"

    @async_lru_cache(ttl_seconds=600)
    async def outer(conn, agency_id: int) -> str:
        await inner(conn, 2)
        await require_retained(conn, agency_id, START)
        # Agency 2's rows go once inner has returned whole; agency 1's floor
        # passes the outer's own read.
        floors.floors[2] = FLOOR
        if agency_id == 1:
            floors.floors[1] = FLOOR
        return "outer"

    with pytest.raises(RangeNotRetained):
        await outer(conn, 1)
    floors.floors.clear()
    floors.reads.clear()
    assert await outer(conn, 3) == "outer"
    # The outer re-reads only its own agency; inner's result came from its cache.
    assert floors.reads == [(conn, 3), (conn, 3)]


async def test_concurrent_cached_calls_each_keep_their_record(floors):
    both_read = asyncio.Event()
    read = []

    @async_lru_cache(ttl_seconds=600)
    async def compute(conn, agency_id: int) -> int:
        await require_retained(conn, agency_id, START)
        read.append(agency_id)
        if len(read) == 2:
            both_read.set()
        await both_read.wait()
        return agency_id

    async def drop_agency_1():
        await both_read.wait()
        floors.floors[1] = FLOOR

    refused, served, _ = await asyncio.gather(
        compute(Connection(), 1), compute(Connection(), 2), drop_agency_1(), return_exceptions=True
    )
    assert isinstance(refused, RangeNotRetained)
    assert served == 2


async def test_the_stages_a_compute_gathers_share_its_record(floors):
    @async_lru_cache(ttl_seconds=600)
    async def compute(conn) -> str:
        async def stage(agency_id: int) -> None:
            await require_retained(conn, agency_id, START)

        await asyncio.gather(stage(1), stage(2))
        floors.floors[2] = FLOOR
        return "partial"

    with pytest.raises(RangeNotRetained):
        await compute(Connection())


async def test_an_ask_tool_whose_floor_rises_mid_read_gives_the_refusal(floors, monkeypatch):
    from api.range import RangeCtx
    from pipeline.query import tools

    async def live_tool(arguments, ctx, conn, agency_id, locale, ch=None):
        await require_retained(conn, agency_id, START)
        floors.floors[agency_id] = FLOOR
        return tools.ToolResult(kind="text", summary="partial")

    monkeypatch.setitem(tools._HANDLERS, "top_n", live_tool)
    ctx = RangeCtx(from_date=START, to_date=FLOOR)
    result = await tools.dispatch("top_n", {}, ctx, Connection(), 1, locale="en")
    assert result.kind == "empty"
    assert result.summary.startswith(f"Detailed readings before {FLOOR.isoformat()} are no longer kept")


def _app(endpoint) -> FastAPI:
    app = FastAPI(dependencies=[Depends(recheck_live_reads, scope="function")])
    app.add_exception_handler(RangeNotRetained, range_not_retained_handler)
    app.get("/live")(endpoint)
    return app


def test_an_uncached_request_whose_floor_rises_mid_read_is_refused(floors):
    async def live():
        await require_retained(Connection(), 1, START)
        floors.floors[1] = FLOOR
        return {"answer": "partial"}

    resp = TestClient(_app(live)).get("/live")
    assert resp.status_code == 422
    assert resp.json()["floor"] == FLOOR.isoformat()


def test_without_a_floor_an_uncached_request_adds_one_floor_read(floors):
    async def live():
        await require_retained(Connection(), 1, START)
        await require_retained(Connection(), 1, FLOOR)
        return {"answer": "whole"}

    resp = TestClient(_app(live)).get("/live")
    assert resp.status_code == 200
    assert [agency for _conn, agency in floors.reads] == [1, 1, 1]


def test_a_request_without_live_reads_reads_no_floor(floors):
    async def aggregates():
        return {"answer": "aggregates"}

    assert TestClient(_app(aggregates)).get("/live").status_code == 200
    assert floors.reads == []


def test_the_app_rechecks_every_request_before_its_response():
    from api.main import app

    assert any(dep.dependency is recheck_live_reads and dep.scope == "function" for dep in app.router.dependencies), (
        app.router.dependencies
    )


async def test_a_checkpoint_refuses_before_a_result_built_from_a_dropped_read_is_stored(floors):
    with retained_reads.recording() as reads:
        await require_retained(Connection(), 1, START)
        floors.floors[1] = FLOOR
        with pytest.raises(RangeNotRetained):
            await retained_reads.checkpoint()
    assert reads.starts


async def test_a_checkpoint_settles_the_reads_it_verified(floors):
    with retained_reads.recording() as reads:
        await require_retained(Connection(), 1, START)
        await retained_reads.checkpoint()
        assert not reads.starts
        floors.floors[1] = FLOOR
    await reads.verify()


async def test_outside_any_computation_nothing_is_noted(floors):
    await require_retained(Connection(), 1, START)
    await retained_reads.checkpoint()
    assert len(floors.reads) == 1
