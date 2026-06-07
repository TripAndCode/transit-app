"""In-process performance registry.

Explicit, human-named timing labels recorded into a per-process registry:
``label -> count / total / max / ring buffer of recent durations``.
Percentiles are computed at read time from the ring buffer (last 500
samples), so steady-state recording cost is one ``perf_counter()`` pair and
a deque append. In-process only — fine for the single-container deploy,
same trade-off as :mod:`pipeline.cache`.

Durations above ``PERF_SLOW_MS`` (env, default 500) emit one WARNING line;
the request_id ContextVar from api.logging_config rides along via the
normal logging format, so slow ops are correlatable to requests.
"""

from __future__ import annotations

import logging
import os
import time
from collections import deque
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from functools import wraps
from typing import Any, AsyncIterator, Awaitable, Callable, TypeVar

T = TypeVar("T")

_log = logging.getLogger(__name__)

_RING_SIZE = 500


def _slow_threshold_ms() -> float:
    """Read env per call so test monkeypatching takes effect without reload."""
    try:
        return float(os.environ.get("PERF_SLOW_MS", "500"))
    except ValueError:
        return 500.0


@dataclass
class _Stat:
    count: int = 0
    total_ms: float = 0.0
    max_ms: float = 0.0
    recent: deque[float] = field(default_factory=lambda: deque(maxlen=_RING_SIZE))


_stats: dict[str, _Stat] = {}
_cache_stats: dict[str, dict[str, int]] = {}


def record(label: str, ms: float) -> None:
    """Record one duration (milliseconds) under ``label``."""
    st = _stats.setdefault(label, _Stat())
    st.count += 1
    st.total_ms += ms
    if ms > st.max_ms:
        st.max_ms = ms
    st.recent.append(ms)
    if ms > _slow_threshold_ms():
        _log.warning("slow op label=%s duration_ms=%d", label, int(ms))


def record_cache(label: str, hit: bool) -> None:
    """Record one cache lookup outcome under ``label``."""
    c = _cache_stats.setdefault(label, {"hits": 0, "misses": 0})
    c["hits" if hit else "misses"] += 1


def timed(label: str) -> Callable[[Callable[..., Awaitable[T]]], Callable[..., Awaitable[T]]]:
    """Decorate an async function so every call is recorded under ``label``.

    Records on exception too — a slow failure is still a slow call.
    """

    def decorator(fn: Callable[..., Awaitable[T]]) -> Callable[..., Awaitable[T]]:
        @wraps(fn)
        async def wrapper(*args: Any, **kwargs: Any) -> T:
            t0 = time.perf_counter()
            try:
                return await fn(*args, **kwargs)
            finally:
                record(label, (time.perf_counter() - t0) * 1000.0)

        return wrapper

    return decorator


@asynccontextmanager
async def timed_block(label: str) -> AsyncIterator[None]:
    """Async context manager variant of :func:`timed` for inner blocks
    and dynamic labels (e.g. ``f"ask.tool.{name}"``)."""
    t0 = time.perf_counter()
    try:
        yield
    finally:
        record(label, (time.perf_counter() - t0) * 1000.0)


def _percentile(sorted_vals: list[float], q: float) -> float:
    """Nearest-rank percentile; ``sorted_vals`` must be non-empty + sorted."""
    idx = min(len(sorted_vals) - 1, max(0, round(q * (len(sorted_vals) - 1))))
    return sorted_vals[idx]


def snapshot() -> dict[str, Any]:
    """JSON-able dump: per-label op stats + per-function cache stats."""
    ops: dict[str, Any] = {}
    for label in sorted(_stats):
        st = _stats[label]
        vals = sorted(st.recent)
        ops[label] = {
            "count": st.count,
            "avg_ms": round(st.total_ms / st.count, 1),
            "p50_ms": round(_percentile(vals, 0.50), 1),
            "p95_ms": round(_percentile(vals, 0.95), 1),
            "max_ms": round(st.max_ms, 1),
        }
    caches: dict[str, Any] = {}
    for label in sorted(_cache_stats):
        c = _cache_stats[label]
        total = c["hits"] + c["misses"]
        caches[label] = {
            "hits": c["hits"],
            "misses": c["misses"],
            "hit_rate": round(c["hits"] / total, 3) if total else 0.0,
        }
    return {"ops": ops, "caches": caches}


def reset() -> None:
    """Clear all recorded stats (bench harness calls this between runs)."""
    _stats.clear()
    _cache_stats.clear()
