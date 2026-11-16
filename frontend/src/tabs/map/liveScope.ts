import { applyScopePatch, type ScopePatch } from "../../api/scope";
import type { ScopeApplied } from "../../components/scope/scopePhrases";

/** The map's own selection: a focused route, its direction and a trip. */
const SELECTION_KEYS = ["route_focus", "route_focus_agency", "trip", "direction"] as const;

/** A scope change on Live, as one URL write. A change to the routes also
 *  drops the map's selection, which can name a route the new filter leaves
 *  out. One write, because two `setSearchParams` calls from one handler each
 *  start from the same params and the second undoes the first. */
export function applyLiveScopePatch(prev: URLSearchParams, patch: ScopePatch): URLSearchParams {
  const next = applyScopePatch(prev, patch);
  if ("routes" in patch) for (const key of SELECTION_KEYS) next.delete(key);
  return next;
}

/** What Live counts by: the routes and the on-time window. It shows the
 *  trips reporting now, so no day, time or timetable condition narrows it.
 *  The period is left out rather than marked unused: Live's sentence says
 *  "live" in its place, so naming it unused would repeat that on every visit. */
export const LIVE_SCOPE_APPLIED: ScopeApplied = {
  routes: true,
  late: true,
  early: true,
  dow: false,
  time_band: false,
  hour: false,
  service: false,
  stop: false,
  dir: false,
};
