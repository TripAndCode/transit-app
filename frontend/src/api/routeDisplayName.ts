import type { Route } from "./types";

/**
 * Prefer route_short_name, then route_long_name, before falling back to the
 * raw route_id (which duplicates the parenthesised code inline and reads
 * worse than either GTFS name field). Mirrors the backend's own
 * COALESCE(NULLIF(short,''), NULLIF(long,''), code) order in
 * api/routers/reports.py's forecast_overview route-label query — empty
 * strings (not just null/undefined) count as "absent" on both sides.
 *
 * Single source of truth for this 3-way fallback: useRouteNames.ts and
 * TabFilterBar.tsx both used to hand-roll this inline, and once diverged
 * (one got a fix the other didn't) until a second pass caught the gap. Any
 * future caller should use this instead of re-deriving the order.
 */
export function routeDisplayName(route: Pick<Route, "route_short_name" | "route_long_name" | "route_id">): string {
  return route.route_short_name || route.route_long_name || route.route_id;
}
