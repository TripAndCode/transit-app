import type { LiveTrip } from "../../api/types";

const CLOCK_SKEW_ALLOWANCE_MS = -60_000;
/** A report older than this is dropped from the view entirely; MapTab's freshness badge turns "stale" at the same age. */
export const MAX_REPORT_AGE_MS = 10 * 60_000;

/** Never label old reports as current trips; reception may stop between polls. */
export function filterLiveRows(rows: LiveTrip[], now: number, routeFilter: string[]): LiveTrip[] {
  return rows.filter((trip) => {
    const age = now - Date.parse(trip.captured_at);
    return age >= CLOCK_SKEW_ALLOWANCE_MS && age <= MAX_REPORT_AGE_MS && (!routeFilter.length || routeFilter.includes(trip.route_code ?? ""));
  });
}
