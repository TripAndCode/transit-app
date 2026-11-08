"""Tiny async LRU+TTL cache for the compute_* functions.

Reports become live queries in v2 (a full ClickHouse scan per request) which
is fast enough for the first hit but wasteful on repeats. Wrap each compute_*
with :func:`async_lru_cache` so identical (agency, ctx, kwargs) requests
served within ``ttl_seconds`` reuse the previous result. Bounded so the
cache never grows beyond ``maxsize`` entries.

The cache is in-process — fine for the single-container deploy. Switch
to a distributed cache only when we run multiple replicas.

A raised failure is never cached. A function that returns a degraded result
instead of raising (a figure left null because a read failed) calls
:func:`do_not_cache`, so that result reaches its caller but the next identical
call computes again rather than being served it for ``ttl_seconds``.
"""

from __future__ import annotations

import asyncio
import functools
import threading
import time
from collections import OrderedDict
from contextvars import ContextVar
from typing import Any, Awaitable, Callable, TypeVar

from pipeline import perf

T = TypeVar("T")

# Every decorated function registers its cache_clear here, for clear_all.
_REGISTERED_CLEARS: list[Callable[[], None]] = []


class _Call:
    """Whether the cached call in progress may store its result."""

    __slots__ = ("cacheable",)

    def __init__(self) -> None:
        self.cacheable = True


# The innermost cached call in progress in this context. A task the call
# starts copies the context, and with it this same object, so a mark made
# inside an asyncio.gather still reaches the call.
_current_call: ContextVar[_Call | None] = ContextVar("async_lru_cache_call", default=None)


def do_not_cache() -> None:
    """Keep the result of the cached call in progress out of its cache.

    The result still reaches the caller and any identical call coalesced onto
    it. Outside a cached call it does nothing. See the module docstring.
    """
    call = _current_call.get()
    if call is not None:
        call.cacheable = False


def clear_all() -> None:
    """Clear every async_lru_cache in the process. The perf debug endpoint
    calls it for cold-run benchmarks, and
    pipeline.reports.definition.stamp_build when analyze records a build, so
    rows served are never older than the build they are stamped with."""
    for clear in _REGISTERED_CLEARS:
        clear()


def async_lru_cache(maxsize: int = 64, ttl_seconds: int = 300):
    """Decorate an async function with bounded LRU + TTL caching.

    Cache key is built from positional args and sorted kwargs items, with
    asyncpg ``Connection`` objects ignored (they are not part of the
    semantic key).

    Concurrent identical misses are coalesced: only the first coroutine
    executes the underlying function; the rest await its Future and count
    as hits (stampede prevention — N concurrent cold overviews previously
    fanned out N×10 pool connections).
    """

    def decorator(fn: Callable[..., Awaitable[T]]) -> Callable[..., Awaitable[T]]:
        _cache: OrderedDict[Any, tuple[float, T]] = OrderedDict()
        # Per-key futures for in-flight computes (stampede coalescing).
        inflight: dict[Any, asyncio.Future[T]] = {}
        # cache_clear() also runs on worker threads (a sync background task's
        # static reload), so every read-modify-write of _cache and inflight's
        # ownership check is made atomic with a clear by this lock, not merely
        # by the event loop.
        lock = threading.Lock()
        label = fn.__name__

        @functools.wraps(fn)
        async def wrapper(*args: Any, **kwargs: Any) -> T:
            key = (
                tuple(_keyable(a) for a in args),
                tuple(sorted((k, _keyable(v)) for k, v in kwargs.items())),
            )
            now = time.monotonic()
            with lock:
                entry = _cache.get(key)
                fresh = entry is not None and now - entry[0] <= ttl_seconds
                if fresh:
                    _cache.move_to_end(key)
                elif entry is not None:
                    del _cache[key]
            if entry is not None and fresh:
                perf.record_cache(label, hit=True)
                return entry[1]

            # Stampede coalescing: if a compute is already in flight for
            # this key, join it rather than launching another.
            #
            # Waiter re-election: CancelGETOnDisconnectMiddleware cancels GET
            # handlers on client disconnect; if that handler is the coalescing
            # leader its Future is cancelled. Waiters use asyncio.shield() so
            # their own Task is NOT cancelled, but the shield unblocks with
            # CancelledError when the underlying Future is cancelled. We must
            # distinguish "leader died" (fut.cancelled()) from "we ourselves
            # were cancelled" (fut still running/done) and re-elect in the
            # former case so healthy waiters still get a result.
            while True:
                fut = inflight.get(key)
                if fut is None:
                    break
                perf.record_cache(label, hit=True)  # coalesced — no recompute
                try:
                    return await asyncio.shield(fut)
                except asyncio.CancelledError:
                    if fut.cancelled():
                        # Leader was cancelled (e.g. client disconnect) — not us.
                        # Re-elect: fall through the loop to become leader or join
                        # the next one. Record a miss for the new leader attempt.
                        perf.record_cache(label, hit=False)
                        continue
                    # We ourselves were cancelled; propagate immediately.
                    raise

            perf.record_cache(label, hit=False)
            fut = asyncio.get_running_loop().create_future()
            # Suppress "exception was never retrieved" warnings when there
            # are zero additional waiters and the compute raises.
            fut.add_done_callback(lambda f: f.exception() if f.done() and not f.cancelled() else None)
            inflight[key] = fut
            succeeded = False
            call = _Call()
            token = _current_call.set(call)
            try:
                value = await fn(*args, **kwargs)
                fut.set_result(value)
                succeeded = True
            except asyncio.CancelledError:
                # Leader was cancelled (e.g. CancelGETOnDisconnectMiddleware).
                # Cancel the future so waiters' shield() unblocks and they can
                # detect fut.cancelled() == True and re-elect a new leader.
                fut.cancel()
                raise
            except BaseException as exc:
                # Other exceptions are forwarded to waiters directly so they
                # surface the real error rather than retrying indefinitely.
                fut.set_exception(exc)
                raise
            finally:
                _current_call.reset(token)
                # A cache_clear() since this compute started empties the slot
                # (a newer leader may hold it since). Then its inputs predate
                # the clear, so it neither caches nor evicts that leader.
                # Only a success its call did not mark is cached, so the next
                # caller retries a failure or a degraded result.
                with lock:
                    if inflight.get(key) is fut:
                        del inflight[key]
                        if succeeded and call.cacheable:
                            _cache[key] = (time.monotonic(), value)
                            _cache.move_to_end(key)
                            while len(_cache) > maxsize:
                                _cache.popitem(last=False)
            return value

        def _cache_clear() -> None:
            with lock:
                _cache.clear()
                inflight.clear()

        wrapper.cache_clear = _cache_clear  # type: ignore[attr-defined]
        _REGISTERED_CLEARS.append(_cache_clear)
        return wrapper

    return decorator


def _keyable(obj: Any) -> Any:
    """Asyncpg connection-like objects are POOLED and per-request — they hash
    by identity, which would make every request a cache miss. Replace them
    with a class-name token so the cache key reflects only the semantic args.
    That includes :class:`pipeline.query.pooled_conn.PooledConnection`, the
    per-request stand-in Ask passes for one.

    Any other unhashable argument (list, dict, ...) raises rather than being
    collapsed to a class-name-only token: two different requests passing
    different unhashable values of the same type would otherwise collide on
    the same cache key and one would silently receive the other's cached
    result. Add explicit handling here (e.g. tuple(a_list),
    frozenset(a_dict.items())) if a future cached function needs one.
    """
    cls = type(obj).__name__
    if cls in {"Connection", "PoolConnectionProxy", "Pool", "PooledConnection"}:
        return f"<conn:{cls}>"
    try:
        hash(obj)
        return obj
    except TypeError:
        raise TypeError(
            f"async_lru_cache: argument of type {cls!r} is not hashable and has no "
            "explicit cache-key handling in pipeline.cache._keyable() — add one instead "
            "of caching under a key that would collapse every distinct value of this "
            "type together."
        ) from None
