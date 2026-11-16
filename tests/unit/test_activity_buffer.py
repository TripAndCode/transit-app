import asyncio
from datetime import date

from api.activity import ActivityBuffer, ActivityKey, AnonymousKey, agency_id_from, flush
from api.middleware.usage import _should_count

_KEY = ActivityKey(1, date(2026, 1, 5), "/api/{agency_id}/overview/summary", "GET", 3)
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


def test_agency_id_outside_1_to_int4_max_is_treated_as_absent():
    # Either would fail the whole flush batch, not just this key: asyncpg
    # can't encode the value as int4, or 0 collides with a NULL agency on the
    # COALESCE(agency_id, 0) unique key.
    assert agency_id_from({"agency_id": "99999999999"}) is None
    assert agency_id_from({"agency_id": "-99999999999"}) is None
    assert agency_id_from({"agency_id": "0"}) is None
    assert agency_id_from({"agency_id": "-3"}) is None
    assert agency_id_from({"agency_id": "1"}) == 1
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
                written.append(args[5])

        app = SimpleNamespace(state=SimpleNamespace(pool=_SlowPool()))
        # Both module buffers outlive a test, and requests from earlier tests
        # in the process leave counts in them. Only this test's one user count
        # may reach the pool.
        activity.ANONYMOUS_BUFFER.drain()
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


def test_only_the_who_am_i_read_of_api_me_is_exempt_from_counting():
    assert not _should_count("/api/me", "GET")
    assert not _should_count("/api/me", "HEAD")
    assert _should_count("/api/me", "DELETE")
    assert _should_count("/api/me", "PATCH")
    assert not _should_count("/api/config", "GET")
    assert not _should_count("/api/auth/logout", "POST")
    assert _should_count("/api/agencies", "GET")
    assert not _should_count("/healthz", "GET")
