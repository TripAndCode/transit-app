import type { LiveTrip } from "../../api/types";
import { CLOCK_SKEW_ALLOWANCE_MS } from "../../utils/clockSkew";

/** A report older than this is dropped from the view entirely; MapTab's freshness badge turns "stale" at the same age. */
export const MAX_REPORT_AGE_MS = 10 * 60_000;

function onRoutes(trip: LiveTrip, routeFilter: string[]): boolean {
  return !routeFilter.length || routeFilter.includes(trip.route_code ?? "");
}

/** Never label old reports as current trips; reception may stop between polls. */
export function filterLiveRows(rows: LiveTrip[], now: number, routeFilter: string[]): LiveTrip[] {
  return rows.filter((trip) => {
    const age = now - Date.parse(trip.captured_at);
    return age >= CLOCK_SKEW_ALLOWANCE_MS && age <= MAX_REPORT_AGE_MS && onRoutes(trip, routeFilter);
  });
}

/** A stalled feed's last poll, whatever its age, for a view that labels it as
 *  the last report rather than as current trips. */
export function filterLastReportedRows(rows: LiveTrip[], routeFilter: string[]): LiveTrip[] {
  return rows.filter((trip) => onRoutes(trip, routeFilter));
}
