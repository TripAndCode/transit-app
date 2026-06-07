"""Perf debug surface. Internal-only: env-gated, out of the OpenAPI schema.

GET  /api/debug/perf        -> pipeline.perf snapshot + pool utilization
POST /api/debug/perf/reset  -> clear registry AND all async_lru_caches
                               (cold-run benchmarking)
"""

import os

from fastapi import APIRouter, HTTPException, Request

from pipeline import cache, perf

# No user dependency — matches sibling read-routers (reports, overview, ask_dashboard).
# The env gate below is the guard; set PERF_DEBUG_ENABLED=false in production.
router = APIRouter(prefix="/api/debug", tags=["debug"], include_in_schema=False)


def _require_enabled() -> None:
    enabled = os.environ.get("PERF_DEBUG_ENABLED", "true").lower() in ("1", "true", "yes")
    if not enabled:
        # 404, not 403: when disabled the surface shouldn't exist at all.
        raise HTTPException(status_code=404, detail="Not found")


@router.get("/perf")
async def perf_snapshot(request: Request):
    _require_enabled()
    snap = perf.snapshot()
    pool = request.app.state.pool
    snap["pool"] = {"size": pool.get_size(), "idle": pool.get_idle_size()}
    return snap


@router.post("/perf/reset")
async def perf_reset():
    _require_enabled()
    perf.reset()
    cache.clear_all()
    return {"status": "reset"}
