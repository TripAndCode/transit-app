import { scopeToQueryString, type Scope } from "./scope";

/** The scope summary answers to the period, days, timetable, routes and the
 *  early bound only (api/routers/scope_summary.py), so its request and cache
 *  key carry nothing else: changing the tolerance or a time condition does
 *  not refetch an identical payload. */
export function scopeSummaryQuery(scope: Scope): string {
  return scopeToQueryString({ ...scope, time_band: "all", hour: null, stop: null, dir: null, late: null });
}
