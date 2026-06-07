"""Perf debug surface. Internal-only: env-gated, out of the OpenAPI schema.

Both endpoints are hidden from the OpenAPI schema (``include_in_schema=False``)
and return 404 when ``PERF_DEBUG_ENABLED`` is not set to a truthy value
(``1``, ``true``, or ``yes``). Set it to ``false`` in production.

No user dependency — matches sibling read-routers (reports, overview,
ask_dashboard). The env gate is the access control.

Routes
------
GET  /api/debug/perf        -- pipeline.perf snapshot + pool utilization.
POST /api/debug/perf/reset  -- clear perf registry AND all async_lru_caches
                               (cold-run benchmarking).
"""

import os
from typing import Any

from fastapi import APIRouter, HTTPException, Request

from pipeline import cache, perf

router = APIRouter(prefix="/api/debug", tags=["debug"], include_in_schema=False)


def _require_enabled() -> None:
    """Raise HTTP 404 when the debug surface is disabled.

    Reads the environment variable on every call so runtime config changes
    and test monkeypatching take effect without restarting the process.
    Uses 404 rather than 403 so that the surface appears non-existent when
    disabled, rather than advertising itself as forbidden.
    """
    enabled = os.environ.get("PERF_DEBUG_ENABLED", "true").lower() in ("1", "true", "yes")
    if not enabled:
        raise HTTPException(status_code=404, detail="Not found")


@router.get("/perf")
async def perf_snapshot(request: Request) -> dict[str, Any]:
    """Return a JSON snapshot of the in-process perf registry plus pool stats.

    Response shape::

        {
          "ops":    { "<label>": { "count", "avg_ms", "p50_ms", "p95_ms", "max_ms" } },
          "caches": { "<label>": { "hits", "misses", "hit_rate" } },
          "pool":   { "size": <int>, "idle": <int> }
        }
    """
    _require_enabled()
    snap = perf.snapshot()
    pool = request.app.state.pool
    snap["pool"] = {"size": pool.get_size(), "idle": pool.get_idle_size()}
    return snap


@router.post("/perf/reset")
async def perf_reset() -> dict[str, str]:
    """Clear the perf registry and all async_lru_caches.

    Intended for cold-run benchmarking: call this before a bench run to
    ensure no warm-cache or accumulated-stat bias in the next snapshot.
    """
    _require_enabled()
    perf.reset()
    cache.clear_all()
    return {"status": "reset"}
