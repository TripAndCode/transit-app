"""Tiny async LRU+TTL cache for the compute_* functions.

Reports become live queries in v2 (1.5M-row scans on Aomori) which is fast
enough for the first hit but wasteful on repeats. Wrap each compute_*
with :func:`async_lru_cache` so identical (agency, ctx, kwargs) requests
served within ``ttl_seconds`` reuse the previous result. Bounded so the
cache never grows beyond ``maxsize`` entries.

The cache is in-process — fine for the single-container deploy. Switch
to a distributed cache only when we run multiple replicas.
"""

from __future__ import annotations

import asyncio
import functools
import time
from collections import OrderedDict
from typing import Any, Awaitable, Callable, TypeVar

from pipeline import perf

T = TypeVar("T")

# Every decorated function registers its cache_clear here so the perf debug
# endpoint can wipe all caches for cold-run benchmarking.
_REGISTERED_CLEARS: list[Callable[[], None]] = []


def clear_all() -> None:
    """Clear every async_lru_cache in the process (bench cold runs)."""
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
        label = fn.__name__

        @functools.wraps(fn)
        async def wrapper(*args: Any, **kwargs: Any) -> T:
            key = (
                tuple(_keyable(a) for a in args),
                tuple(sorted((k, _keyable(v)) for k, v in kwargs.items())),
            )
            now = time.monotonic()
            entry = _cache.get(key)
            if entry is not None:
                ts, value = entry
                if now - ts <= ttl_seconds:
                    _cache.move_to_end(key)
                    perf.record_cache(label, hit=True)
                    return value
                del _cache[key]

            # Stampede coalescing: if a compute is already in flight for
            # this key, join it rather than launching another.
            fut = inflight.get(key)
            if fut is not None:
                perf.record_cache(label, hit=True)  # coalesced — no recompute
                return await asyncio.shield(fut)

            perf.record_cache(label, hit=False)
            fut = asyncio.get_running_loop().create_future()
            # Suppress "exception was never retrieved" warnings when there
            # are zero additional waiters and the compute raises.
            fut.add_done_callback(lambda f: f.exception() if f.done() and not f.cancelled() else None)
            inflight[key] = fut
            try:
                value = await fn(*args, **kwargs)
                fut.set_result(value)
            except BaseException as exc:
                fut.set_exception(exc)
                raise
            finally:
                inflight.pop(key, None)

            # Cache only on success (exceptions must not be cached so that
            # the next caller retries the underlying function).
            _cache[key] = (time.monotonic(), value)
            _cache.move_to_end(key)
            while len(_cache) > maxsize:
                _cache.popitem(last=False)
            return value

        def _cache_clear() -> None:
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
    """
    cls = type(obj).__name__
    if cls in {"Connection", "PoolConnectionProxy", "Pool"}:
        return f"<conn:{cls}>"
    try:
        hash(obj)
        return obj
    except TypeError:
        return f"<unhashable:{cls}>"
