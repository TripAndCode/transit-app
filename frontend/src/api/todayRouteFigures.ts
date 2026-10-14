import type { RouteSummary } from "./types";

/** Today's mean delay in minutes, over every route weighted by observed
 *  trips, or one route's own; null when no trip in scope was observed today. */
export function todayMeanMinutes(routes: readonly RouteSummary[] | undefined, code?: string): number | null {
  let trips = 0;
  let weighted = 0;
  for (const row of routes ?? []) {
    if ((code != null && row.route_code !== code) || row.trips_observed <= 0) continue;
    trips += row.trips_observed;
    weighted += row.avg_delay_sec * row.trips_observed;
  }
  return trips > 0 ? weighted / trips / 60 : null;
}

/** How many routes run later than usual today: the triage's `anomaly`
 *  bucket, the same one the live map colours by. */
export function unusualRouteCount(routes: readonly RouteSummary[] | undefined): number {
  return (routes ?? []).filter((row) => row.bucket === "anomaly").length;
}
