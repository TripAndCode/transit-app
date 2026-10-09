import type { RouteSummary } from "./types";

/** Today's mean delay in minutes, weighted by observed trips, over every
 *  route or over one route's rows (a route runs as one row per service
 *  type); null when no trip in scope was observed today. */
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
 *  bucket, the same one the live map colours by, counted once per route
 *  however many service types it runs as. */
export function unusualRouteCount(routes: readonly RouteSummary[] | undefined): number {
  return new Set((routes ?? []).filter((row) => row.bucket === "anomaly").map((row) => row.route_code)).size;
}
