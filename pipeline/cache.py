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

import functools
import time
from collections import OrderedDict
from typing import Any, Awaitable, Callable, TypeVar

T = TypeVar("T")


def async_lru_cache(maxsize: int = 64, ttl_seconds: int = 300):
    """Decorate an async function with bounded LRU + TTL caching.

    Cache key is built from positional args and sorted kwargs items, with
    asyncpg ``Connection`` objects ignored (they are not part of the
    semantic key).
    """

    def decorator(fn: Callable[..., Awaitable[T]]) -> Callable[..., Awaitable[T]]:
        cache: OrderedDict[Any, tuple[float, T]] = OrderedDict()

        @functools.wraps(fn)
        async def wrapper(*args: Any, **kwargs: Any) -> T:
            key = (
                tuple(_keyable(a) for a in args),
                tuple(sorted((k, _keyable(v)) for k, v in kwargs.items())),
            )
            now = time.monotonic()
            entry = cache.get(key)
            if entry is not None:
                ts, value = entry
                if now - ts <= ttl_seconds:
                    cache.move_to_end(key)
                    return value
                # expired
                del cache[key]
            value = await fn(*args, **kwargs)
            cache[key] = (now, value)
            cache.move_to_end(key)
            while len(cache) > maxsize:
                cache.popitem(last=False)
            return value

        wrapper.cache_clear = cache.clear  # type: ignore[attr-defined]
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
