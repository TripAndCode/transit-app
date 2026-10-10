import type { LiveTrip } from "../../api/types";

/**
 * Mean live departure delay (whole seconds) of one route's currently reporting
 * rows; 0 when no route is selected or it has no rows. The route overlay's
 * severity colour is keyed to this value.
 */
export function meanRouteDelaySec(liveRows: LiveTrip[], routeCode: string | null): number {
  if (routeCode == null) return 0;
  const rows = liveRows.filter((trip) => trip.route_code === routeCode);
  if (rows.length === 0) return 0;
  return Math.round(rows.reduce((sum, trip) => sum + trip.dep_delay, 0) / rows.length);
}
