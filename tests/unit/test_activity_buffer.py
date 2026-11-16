import asyncio
from datetime import date

from api.activity import ActivityBuffer, ActivityKey, AnonymousKey, agency_id_from, flush

_KEY = ActivityKey(1, date(2026, 1, 5), "/api/{agency_id}/overview/summary", "GET", 3, False)
_ANON_KEY = AnonymousKey(date(2026, 1, 5), "/api/agencies", "GET", None)


def test_counts_add_up_per_key_and_errors_separately():
    buf = ActivityBuffer()
    buf.record(_KEY, is_error=False)
    buf.record(_KEY, is_error=True)
    assert buf.drain() == {_KEY: [2, 1]}


def test_drain_empties_the_buffer():
    buf = ActivityBuffer()
    buf.record(_KEY, is_error=False)
    buf.drain()
    assert buf.drain() == {}


def test_the_buffer_is_generic_over_the_anonymous_key_too():
    buf: ActivityBuffer[AnonymousKey] = ActivityBuffer()
    buf.record(_ANON_KEY, is_error=False)
    buf.record(_ANON_KEY, is_error=True)
    assert buf.drain() == {_ANON_KEY: [2, 1]}


def test_agency_id_comes_from_the_path_parameter_when_it_is_a_number():
    assert agency_id_from({"agency_id": "7"}) == 7
    assert agency_id_from({"agency_id": "nope"}) is None
    assert agency_id_from({}) is None
    assert agency_id_from(None) is None


def test_agency_id_outside_postgres_int4_range_is_treated_as_absent():
    # A signed-out caller supplies this path segment directly; a value
    # asyncpg can't encode as int4 would otherwise fail the whole flush
    # batch, not just this one key.
    assert agency_id_from({"agency_id": "99999999999"}) is None
    assert agency_id_from({"agency_id": "-99999999999"}) is None
    assert agency_id_from({"agency_id": str(2**31 - 1)}) == 2**31 - 1
    assert agency_id_from({"agency_id": str(2**31)}) is None


class _FailingPool:
    async def execute(self, *args):
        raise ConnectionError("db down")


def test_a_failed_flush_drops_its_batch_instead_of_keeping_it():
    buf = ActivityBuffer()
    buf.record(_KEY, is_error=False)
    assert asyncio.run(flush(buf, _FailingPool())) == 0
    assert buf.drain() == {}


def test_stopping_during_a_periodic_flush_lets_that_write_finish(monkeypatch):
    """A shutdown that lands mid-write must not cancel it: the batch has
    already left the buffer, so an interrupted write would lose it silently."""
    from types import SimpleNamespace

    from api import activity

    monkeypatch.setattr(activity, "FLUSH_INTERVAL_SECONDS", 0.0)
    written: list[list[int]] = []

    async def scenario():
        started, release = asyncio.Event(), asyncio.Event()

        class _SlowPool:
            async def execute(self, sql, *args):
                started.set()
                await release.wait()
                written.append(args[6])

        app = SimpleNamespace(state=SimpleNamespace(pool=_SlowPool()))
        activity.BUFFER.drain()
        activity.BUFFER.record(_KEY, is_error=False)
        activity.start(app)
        await started.wait()
        stopping = asyncio.create_task(activity.stop(app))
        await asyncio.sleep(0)
        release.set()
        await stopping
        for _ in range(3):
            await asyncio.sleep(0)

    asyncio.run(scenario())
    assert written == [[1]]
