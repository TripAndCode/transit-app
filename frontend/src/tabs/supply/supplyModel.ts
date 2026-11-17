import type { SupplyAgency, SupplyStop } from "./supplyApi";

export type SupplyMode = "departures" | "gap";

/** The service-day hours a band may start and end on: from the 04:00
 *  cutover through the night's after-midnight hours (24 = 0:00) to the next
 *  cutover, as the server accepts them. */
export const FIRST_HOUR = 4;
export const LAST_HOUR = 28;
export const DEFAULT_BAND = { fromHour: 10, toHour: 16 } as const;
export const GAP_THRESHOLDS_MIN = [20, 30, 60, 90, 120] as const;
export const DEFAULT_GAP_MIN = 60;

/** Departures-per-hour classes, lower bounds; the last, 3 an hour, is the
 *  frequency the ministry's trunk-line example names. */
const PER_HOUR_STEPS = [1, 2, 3] as const;

/** A service-day hour as a clock label: 24 reads 24:00, the night's midnight. */
export function hourLabel(hour: number): string {
  return `${hour}:00`;
}

/** Service-day seconds as H:MM, keeping the after-midnight hours past 24. */
export function serviceClock(seconds: number): string {
  const minutes = Math.floor(seconds / 60);
  return `${Math.floor(minutes / 60)}:${String(minutes % 60).padStart(2, "0")}`;
}

/** The class of a stop's departures per hour: 0 for none, then 1 to 4 by PER_HOUR_STEPS. */
export function perHourClass(perHour: number): number {
  if (perHour <= 0) return 0;
  return 1 + PER_HOUR_STEPS.filter((step) => perHour >= step).length;
}

function isPlaced(stop: SupplyStop): stop is SupplyStop & { lat: number; lon: number } {
  return stop.lat != null && stop.lon != null && Number.isFinite(stop.lat) && Number.isFinite(stop.lon);
}

/** Whether a stop's longest gap passes the threshold: a gap exactly at it does not. */
export function overGap(stop: SupplyStop, thresholdMin: number): boolean {
  return stop.longest_gap_min > thresholdMin;
}

/** One stop across operators: stop ids are unique only within an operator. */
export function stopKey(stop: SupplyStop): string {
  return `${stop.agency_id}:${stop.stop_id}`;
}

export function supplyFeatures(stops: readonly SupplyStop[], thresholdMin: number): GeoJSON.FeatureCollection {
  return {
    type: "FeatureCollection",
    features: stops.filter(isPlaced).map((stop) => ({
      type: "Feature",
      properties: {
        key: stopKey(stop),
        per_hour_class: perHourClass(stop.per_hour),
        over_gap: overGap(stop, thresholdMin),
      },
      geometry: { type: "Point", coordinates: [stop.lon, stop.lat] },
    })),
  };
}

export interface SupplySummary {
  stops: number;
  overGap: number;
  unplaced: number;
  readAgencies: number;
}

export function supplySummary(
  agencies: readonly SupplyAgency[],
  stops: readonly SupplyStop[],
  thresholdMin: number,
): SupplySummary {
  return {
    stops: stops.length,
    overGap: stops.filter((stop) => overGap(stop, thresholdMin)).length,
    unplaced: stops.filter((stop) => !isPlaced(stop)).length,
    readAgencies: agencies.filter((agency) => agency.status === "ok").length,
  };
}

/** [west, south, east, north] around every placed stop, or null with none. */
export function stopBounds(stops: readonly SupplyStop[]): [number, number, number, number] | null {
  const placed = stops.filter(isPlaced);
  if (!placed.length) return null;
  const lons = placed.map((s) => s.lon);
  const lats = placed.map((s) => s.lat);
  return [Math.min(...lons), Math.min(...lats), Math.max(...lons), Math.max(...lats)];
}
