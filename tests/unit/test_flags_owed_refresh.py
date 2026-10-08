"""The re-read `invalidate()` owes the next async reader, under concurrency.

A burst of requests right after an admin write all find the read owed. They
must share one database read rather than each opening its own connection,
without that sharing letting any of them settle for data that predates the
write, and without one reader's cancellation or a failed read stranding the
rest.

Synchronisation is by events, never by sleeping: the first stand-in read
blocks until the test releases it, so "a read is in flight" is a state the
test controls rather than a timing it hopes for.
"""

from __future__ import annotations

import asyncio
import threading

import pytest

from pipeline import flags

KEY = "weather_ingest_enabled"
READERS = 8
_WAIT_SECONDS = 5


@pytest.fixture(autouse=True)
def _no_real_database(monkeypatch):
    """Every read is stubbed below; an unreachable URL keeps a stray real one off any database."""
    monkeypatch.setenv("DATABASE_URL", "postgresql://nobody@127.0.0.1:1/nonexistent")


class _StagedLoad:
    """A stand-in `_load_overrides` returning (or raising) `outcomes` in call order.

    Only the first call blocks, until `release()`, so that call is the read
    left in flight while the test acts.
    """

    def __init__(self, *outcomes: dict | BaseException) -> None:
        self._outcomes = outcomes
        self._lock = threading.Lock()
        self.calls = 0
        self.first_call_entered = threading.Event()
        self._released = threading.Event()

    def release(self) -> None:
        self._released.set()

    def __call__(self) -> dict:
        with self._lock:
            index = self.calls
            self.calls += 1
        if index == 0:
            self.first_call_entered.set()
            assert self._released.wait(_WAIT_SECONDS), "the test never released the first read"
        outcome = self._outcomes[min(index, len(self._outcomes) - 1)]
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome


def _override(reason: str, value: bool = True) -> dict:
    return {KEY: (value, reason, 1, None)}


def _warm_then_stage(monkeypatch, *outcomes: dict | BaseException) -> _StagedLoad:
    """Cache a pre-write value, then make every later read go through a `_StagedLoad`."""
    monkeypatch.setattr(flags, "_load_overrides", lambda: _override("before-the-write", value=False))
    flags.warm()
    load = _StagedLoad(*outcomes)
    monkeypatch.setattr(flags, "_load_overrides", load)
    return load


async def _until_first_read_is_in_flight(load: _StagedLoad) -> None:
    # Yielding here also lets every reader task created before the call run
    # up to its wait on the shared read.
    assert await asyncio.to_thread(load.first_call_entered.wait, _WAIT_SECONDS)


async def test_concurrent_async_readers_after_invalidate_share_one_read(monkeypatch):
    load = _warm_then_stage(monkeypatch, _override("after-the-write"))
    flags.invalidate()

    readers = [asyncio.create_task(flags.aget_flag_state(KEY)) for _ in range(READERS)]
    await _until_first_read_is_in_flight(load)
    load.release()
    states = await asyncio.gather(*readers)

    assert load.calls == 1, "each concurrent reader opened its own database read"
    assert {state.reason for state in states} == {"after-the-write"}
    assert flags._refresh_owed is False


def test_readers_on_different_event_loops_share_one_read(monkeypatch):
    """Sharing is process-wide, not per event loop: the database round trip is
    what is being saved, and it costs the same whichever loop asked."""
    load = _warm_then_stage(monkeypatch, _override("after-the-write"))
    flags.invalidate()

    results: dict[str, flags.FlagState] = {}
    joined = {"first": threading.Event(), "second": threading.Event()}

    def read_on_own_loop(name: str) -> None:
        async def main() -> None:
            reader = asyncio.create_task(flags.aget_flag_state(KEY))
            await asyncio.sleep(0)  # let the reader reach its wait
            joined[name].set()
            results[name] = await reader

        asyncio.run(main())

    first = threading.Thread(target=read_on_own_loop, args=("first",))
    first.start()
    assert load.first_call_entered.wait(_WAIT_SECONDS)
    second = threading.Thread(target=read_on_own_loop, args=("second",))
    second.start()
    assert joined["second"].wait(_WAIT_SECONDS)
    load.release()
    first.join(_WAIT_SECONDS)
    second.join(_WAIT_SECONDS)

    assert load.calls == 1, "a reader on a second event loop opened its own database read"
    assert {state.reason for state in results.values()} == {"after-the-write"}
    assert set(results) == {"first", "second"}


async def test_an_invalidate_during_the_shared_read_forces_another(monkeypatch):
    """The in-flight read began before the second write, so its result is
    discarded -- and its waiters must go on to the read that sees that write,
    not settle for the value cached before either."""
    load = _warm_then_stage(monkeypatch, _override("first-write"), _override("second-write"))
    flags.invalidate()

    readers = [asyncio.create_task(flags.aget_flag_state(KEY)) for _ in range(READERS)]
    await _until_first_read_is_in_flight(load)
    flags.invalidate()
    load.release()
    states = await asyncio.gather(*readers)

    assert load.calls == 2
    assert {state.reason for state in states} == {"second-write"}
    assert flags._refresh_owed is False


async def test_a_reader_after_a_newer_invalidate_does_not_wait_on_the_older_read(monkeypatch):
    """A read that began before the latest write can only be discarded, so a
    reader arriving after that write starts its own rather than joining it."""
    load = _warm_then_stage(monkeypatch, _override("first-write"), _override("second-write"))
    flags.invalidate()

    early = asyncio.create_task(flags.aget_flag_state(KEY))
    await _until_first_read_is_in_flight(load)
    flags.invalidate()

    late = await asyncio.wait_for(flags.aget_flag_state(KEY), _WAIT_SECONDS)
    assert late.reason == "second-write"
    assert load.calls == 2

    load.release()
    assert (await early).reason == "second-write"
    assert load.calls == 2, "the superseded read's waiter read the database again"


async def test_writes_superseding_every_read_cannot_hold_a_reader_indefinitely(monkeypatch):
    """Each superseded read sends its waiters round again, so writes landing
    mid-read every time would otherwise keep a request waiting for as long
    as they kept coming."""
    monkeypatch.setattr(flags, "_load_overrides", lambda: _override("before-the-write", value=False))
    flags.warm()
    calls = 0

    def load_then_supersede() -> dict:
        nonlocal calls
        calls += 1
        flags.invalidate()  # another admin write landing mid-read
        return _override("never-committed")

    monkeypatch.setattr(flags, "_load_overrides", load_then_supersede)
    flags.invalidate()

    state = await asyncio.wait_for(flags.aget_flag_state(KEY), _WAIT_SECONDS)
    assert calls == flags._OWED_REFRESH_ROUNDS
    assert state.reason == "before-the-write", "a read whose result was discarded was served anyway"
    assert flags._refresh_owed is True


async def test_a_failed_shared_read_leaves_the_read_owed_for_the_next_reader(monkeypatch):
    load = _warm_then_stage(monkeypatch, RuntimeError("read failed"), _override("after-the-write"))
    flags.invalidate()

    readers = [asyncio.create_task(flags.aget_flag_state(KEY)) for _ in range(READERS)]
    await _until_first_read_is_in_flight(load)
    load.release()
    outcomes = await asyncio.gather(*readers, return_exceptions=True)

    assert load.calls == 1
    assert all(isinstance(outcome, RuntimeError) for outcome in outcomes)
    assert flags._refresh_owed is True, "a read that never committed cleared the owed marker"

    retried = await asyncio.wait_for(flags.aget_flag_state(KEY), _WAIT_SECONDS)
    assert retried.reason == "after-the-write"
    assert load.calls == 2
    assert flags._refresh_owed is False


async def test_cancelling_one_waiter_does_not_cancel_the_shared_read(monkeypatch):
    load = _warm_then_stage(monkeypatch, _override("after-the-write"))
    flags.invalidate()

    readers = [asyncio.create_task(flags.aget_flag_state(KEY)) for _ in range(READERS)]
    await _until_first_read_is_in_flight(load)
    readers[0].cancel()
    await asyncio.sleep(0)
    load.release()
    outcomes = await asyncio.gather(*readers, return_exceptions=True)

    assert isinstance(outcomes[0], asyncio.CancelledError)
    assert {state.reason for state in outcomes[1:]} == {"after-the-write"}
    assert load.calls == 1
    assert flags._refresh_owed is False
