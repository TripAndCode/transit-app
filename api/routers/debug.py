"""Perf debug surface. Internal-only: flag-gated, out of the OpenAPI schema.

Both endpoints are hidden from the OpenAPI schema (``include_in_schema=False``)
and return 404 unless the ``perf_debug_enabled`` flag resolves truthy --
either a ``feature_flags`` override, or its ``PERF_DEBUG_ENABLED`` env var
when there is none (see ``pipeline.flags``).

**Disabled by default.** Set ``PERF_DEBUG_ENABLED=true`` in your dev ``.env``
to enable. The reset endpoint wipes all caches, which is a cheap DoS lever
if exposed, so it additionally requires an authenticated admin and is
CSRF-guarded; the read-only snapshot has no user dependency, matching sibling
read-routers (reports, overview, ask_dashboard).

The flag gate is a router-level dependency so it runs ahead of the per-route
auth dependency. A disabled surface must answer 404 to every caller — an
anonymous 401 would tell a prober the route exists.

Routes
------
GET  /api/debug/perf        -- pipeline.perf snapshot + pool utilization.
POST /api/debug/perf/reset  -- clear perf registry AND all async_lru_caches
                               (cold-run benchmarking); admin + CSRF guarded.
"""

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request

from api.security import User, csrf_guard, require_admin
from pipeline import cache, perf
from pipeline.flags import flag


def _require_enabled() -> None:
    """Raise HTTP 404 when the debug surface is disabled.

    Reads the flag on every call (DB override, else the env var) so a
    runtime toggle and test monkeypatching both take effect without
    restarting the process. Uses 404 rather than 403 so that the surface
    appears non-existent when disabled, rather than advertising itself as
    forbidden.
    """
    if not flag("perf_debug_enabled", False):
        raise HTTPException(status_code=404, detail="Not found")


router = APIRouter(
    prefix="/api/debug",
    tags=["debug"],
    include_in_schema=False,
    dependencies=[Depends(_require_enabled)],
)


@router.get("/perf", response_model=None)
async def perf_snapshot(request: Request) -> dict[str, Any]:
    """Return a JSON snapshot of the in-process perf registry plus pool stats.

    Response shape::

        {
          "ops":    { "<label>": { "count", "avg_ms", "p50_ms", "p95_ms", "max_ms" } },
          "caches": { "<label>": { "hits", "misses", "hit_rate" } },
          "pool":   { "size": <int>, "idle": <int> }
        }
    """
    snap = perf.snapshot()
    pool = request.app.state.pool
    snap["pool"] = {"size": pool.get_size(), "idle": pool.get_idle_size()}
    return snap


@router.post("/perf/reset")
async def perf_reset(request: Request, admin: User = Depends(require_admin)) -> dict[str, str]:
    """Clear the perf registry and all async_lru_caches.

    Intended for cold-run benchmarking: call this before a bench run to
    ensure no warm-cache or accumulated-stat bias in the next snapshot.
    Mutating and cache-wiping, so it requires an authenticated admin
    (``require_admin``) and is CSRF-guarded like other mutating routes.
    """
    csrf_guard(request)
    perf.reset()
    cache.clear_all()
    return {"status": "reset"}
