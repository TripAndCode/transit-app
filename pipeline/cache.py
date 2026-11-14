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
:func:`cache_briefly`, so that result is cached for
:data:`DEGRADED_TTL_SECONDS` rather than ``ttl_seconds``. It heals within that
short TTL once the failed read works again, while identical requests during an
overload are served it instead of each repeating the reads that failed. Only a
transient failure is marked: one the request itself causes, such as a result
past a row cap, recurs on every retry, so its degraded result keeps
``ttl_seconds``.
"""

from __future__ import annotations

import asyncio
import functools
import inspect
import threading
import time
from collections import OrderedDict
from contextvars import ContextVar
from typing import Any, Awaitable, Callable, Iterable, Literal, TypeVar

from pipeline import perf

T = TypeVar("T")

# Every decorated function registers its cache_clear here, for clear_all.
_REGISTERED_CLEARS: list[Callable[[], None]] = []

# Each decorated cache's eviction for a rebuild: given the agencies whose
# aggregates were rewritten, it drops the entries a rebuild can have made stale.
_REGISTERED_EVICTORS: list[Callable[[frozenset[Any]], None]] = []

# The clears in _REGISTERED_CLEARS that belong to a decorated cache, which
# evict_for_rebuild replaces with that cache's own eviction.
_DECORATED_CLEARS: set[Callable[[], None]] = set()

# An agency a key does not name reliably; such an entry is evicted by every rebuild.
_UNREADABLE: Any = object()

#: How a cache is treated when analyze rewrites aggregates: ``"all"`` drops
#: every entry, or with an ``agency_arg`` only that agency's (and any whose key
#: names no agency); ``"never"`` keeps them, for rows that do not come from the
#: aggregates and so no rebuild can make stale.
Rebuild = Literal["all", "never"]

# The clock every entry's age is read on; a module attribute so a test can move it.
_monotonic = time.monotonic

#: How long a result marked by :func:`cache_briefly` is cached, unless the
#: decorator's own TTL is shorter.
DEGRADED_TTL_SECONDS = 30


class _Call:
    """Whether the cached call in progress marked its result as degraded."""

    __slots__ = ("brief",)

    def __init__(self) -> None:
        self.brief = False


# The innermost cached call in progress in this context. A task the call
# starts copies the context, and with it this same object, so a mark made
# inside an asyncio.gather still reaches the call.
_current_call: ContextVar[_Call | None] = ContextVar("async_lru_cache_call", default=None)


def cache_briefly() -> None:
    """Cache the result of the cached call in progress for
    :data:`DEGRADED_TTL_SECONDS` only. Outside a cached call it does nothing.
    See the module docstring.
    """
    call = _current_call.get()
    if call is not None:
        call.brief = True


def clear_all() -> None:
    """Clear every async_lru_cache in the process. The perf debug endpoint
    calls it for cold-run benchmarks, pipeline.live_filing when an agency's
    archive state moves, and pipeline.reports.definition.stamp_build the first
    time a process looks at the builds; a later build clears only what
    :func:`evict_for_rebuild` names."""
    for clear in _REGISTERED_CLEARS:
        clear()


def evict_for_rebuild(agency_ids: Iterable[Any]) -> None:
    """Drop what a rebuild of *agency_ids*' aggregates can have made stale: those
    agencies' entries, every entry whose key names no agency (the board's, and
    any whose agency cannot be read from the key), and every cache registered
    with a plain clear. Entries of other agencies stay, as do the caches of
    rows that do not come from the aggregates (``rebuild="never"``)."""
    agencies = frozenset(agency_ids)
    for evict in _REGISTERED_EVICTORS:
        evict(agencies)
    for clear in _REGISTERED_CLEARS:
        if clear not in _DECORATED_CLEARS:
            clear()


def async_lru_cache(
    maxsize: int = 64, ttl_seconds: int = 300, *, agency_arg: str | None = None, rebuild: Rebuild = "all"
):
    """Decorate an async function with bounded LRU + TTL caching.

    *agency_arg* names the parameter that carries the agency an entry belongs
    to, read back from the key whether the caller passed it positionally or by
    keyword, so :func:`evict_for_rebuild` can drop one agency's entries. A cache
    with none is evicted whole by every rebuild unless *rebuild* is ``"never"``.

    Cache key is built from positional args and sorted kwargs items, with
    asyncpg ``Connection`` objects ignored (they are not part of the
    semantic key).

    Concurrent identical misses are coalesced: only the first coroutine
    executes the underlying function; the rest await its Future and count
    as hits (stampede prevention — N concurrent cold overviews previously
    fanned out N×10 pool connections).
    """

    def decorator(fn: Callable[..., Awaitable[T]]) -> Callable[..., Awaitable[T]]:
        agency_of = _agency_reader(fn, agency_arg) if agency_arg is not None else None
        # key -> (the monotonic time the entry expires at, the result)
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
            now = _monotonic()
            with lock:
                entry = _cache.get(key)
                fresh = entry is not None and now <= entry[0]
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
                # Only a success is cached, so the next caller retries a failure.
                with lock:
                    if inflight.get(key) is fut:
                        del inflight[key]
                        if succeeded:
                            ttl = min(ttl_seconds, DEGRADED_TTL_SECONDS) if call.brief else ttl_seconds
                            _cache[key] = (_monotonic() + ttl, value)
                            _cache.move_to_end(key)
                            while len(_cache) > maxsize:
                                _cache.popitem(last=False)
            return value

        def _cache_clear() -> None:
            with lock:
                _cache.clear()
                inflight.clear()

        def _evict(agencies: frozenset[Any]) -> None:
            if rebuild == "never":
                return
            if agency_of is None:
                _cache_clear()
                return
            with lock:
                for store in (_cache, inflight):
                    for key in [k for k in store if (a := agency_of(k)) is _UNREADABLE or a is None or a in agencies]:
                        del store[key]

        wrapper.cache_clear = _cache_clear  # type: ignore[attr-defined]
        _REGISTERED_CLEARS.append(_cache_clear)
        _DECORATED_CLEARS.add(_cache_clear)
        _REGISTERED_EVICTORS.append(_evict)
        return wrapper

    return decorator


def _agency_reader(fn: Callable[..., Any], name: str) -> Callable[[Any], Any]:
    """A reader of the agency *name* from one of *fn*'s cache keys (positional
    args, then sorted keyword items), or :data:`_UNREADABLE` for a key that does
    not carry it. Raises at decoration when *name* cannot be passed by keyword,
    since no key could then be trusted to carry it."""
    params = list(inspect.signature(fn).parameters.values())
    index = next((i for i, p in enumerate(params) if p.name == name), None)
    if index is None or params[index].kind not in (
        inspect.Parameter.POSITIONAL_OR_KEYWORD,
        inspect.Parameter.KEYWORD_ONLY,
    ):
        raise TypeError(f"{fn.__qualname__}: agency_arg {name!r} must be a named, keyword-passable parameter")
    keyword_only = params[index].kind is inspect.Parameter.KEYWORD_ONLY
    if not keyword_only and any(p.kind is inspect.Parameter.VAR_POSITIONAL for p in params[:index]):
        raise TypeError(f"{fn.__qualname__}: agency_arg {name!r} must not follow *args")
    default = params[index].default

    def read(key: Any) -> Any:
        args, kwargs = key
        for k, v in kwargs:
            if k == name:
                return v
        if not keyword_only and index < len(args):
            return args[index]
        return _UNREADABLE if default is inspect.Parameter.empty else default

    return read


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
