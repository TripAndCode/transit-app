import type { ExpressionSpecification } from "maplibre-gl";
import type { SupplyMapResponse } from "./supplyApi";

export type SupplyMode = "departures" | "gap";

/** The service-day hours a band may start and end on: from the 04:00
 *  cutover through the night's after-midnight hours (24 = 0:00) to the next
 *  cutover, as the server accepts them (pipeline.reports.stop_supply). */
export const FIRST_HOUR = 4;
export const LAST_HOUR = FIRST_HOUR + 24;
export const DEFAULT_BAND = { fromHour: 10, toHour: 16 } as const;
export const GAP_THRESHOLDS_MIN = [20, 30, 60, 90, 120] as const;
export const DEFAULT_GAP_MIN = 60;

/** Departures-per-hour class bounds; the last, 3 an hour, is the frequency
 *  the ministry's trunk-line example names. Classes run 0 (no departure),
 *  1 (under the first bound) and up to one past the last bound. */
const PER_HOUR_STEPS = [1, 2, 3] as const;
export const PER_HOUR_CLASSES = PER_HOUR_STEPS.length + 2;

/** One stop the map draws, unpacked from an operator's columns. */
export interface MapStop {
  agencyId: number;
  stopId: string;
  lat: number | null;
  lon: number | null;
  departures: number;
  longestGapMin: number;
}

/** Every OK operator's stops, from the columns the server sends them in. */
export function mapStops(response: SupplyMapResponse | undefined): MapStop[] {
  const stops: MapStop[] = [];
  for (const agency of response?.agencies ?? []) {
    const columns = agency.stops;
    if (!columns) continue;
    columns.stop_id.forEach((stopId, i) => {
      stops.push({
        agencyId: agency.agency_id,
        stopId,
        lat: columns.lat[i],
        lon: columns.lon[i],
        departures: columns.departures[i],
        longestGapMin: columns.longest_gap_min[i],
      });
    });
  }
  return stops;
}

/** A service-day hour as a clock label: 24 reads 24:00, the night's midnight. */
export function hourLabel(hour: number): string {
  return `${hour}:00`;
}

/** Service-day seconds as H:MM, keeping the after-midnight hours past 24. */
export function serviceClock(seconds: number): string {
  const minutes = Math.floor(seconds / 60);
  return `${Math.floor(minutes / 60)}:${String(minutes % 60).padStart(2, "0")}`;
}

function isPlaced(stop: MapStop): stop is MapStop & { lat: number; lon: number } {
  return stop.lat != null && stop.lon != null && Number.isFinite(stop.lat) && Number.isFinite(stop.lon);
}

/** Whether a stop's longest gap passes the threshold: a gap exactly at it does not. */
function overGap(stop: MapStop, thresholdMin: number): boolean {
  return stop.longestGapMin > thresholdMin;
}

/** One stop across operators: stop ids are unique only within an operator. */
export function stopKey(stop: { agencyId: number; stopId: string }): string {
  return `${stop.agencyId}:${stop.stopId}`;
}

/** The placed stops with the raw figures the paint expressions class, so a
 *  change of mode, threshold or theme only repaints. */
export function supplyFeatures(stops: readonly MapStop[]): GeoJSON.FeatureCollection {
  return {
    type: "FeatureCollection",
    features: stops.filter(isPlaced).map((stop) => ({
      type: "Feature",
      properties: { key: stopKey(stop), departures: stop.departures, gap: stop.longestGapMin },
      geometry: { type: "Point", coordinates: [stop.lon, stop.lat] },
    })),
  };
}

/** A paint value by departures-per-hour class, `byClass[i]` for class i:
 *  0 with no departure, then by PER_HOUR_STEPS over a band of `hours`. */
export function perHourClassExpression(hours: number, byClass: readonly number[]): ExpressionSpecification {
  const perHour: ExpressionSpecification = ["/", ["get", "departures"], hours];
  const steps = PER_HOUR_STEPS.flatMap((bound, i) => [bound, byClass[i + 2]]);
  return ["case", ["==", ["get", "departures"], 0], byClass[0], ["step", perHour, byClass[1], ...steps]];
}

/** Whether a feature's gap passes the threshold, as overGap tells it. */
export function overGapExpression(thresholdMin: number): ExpressionSpecification {
  return [">", ["get", "gap"], thresholdMin];
}

export interface SupplySummary {
  stops: number;
  overGap: number;
  unplaced: number;
  readAgencies: number;
}

export function supplySummary(
  response: SupplyMapResponse | undefined,
  stops: readonly MapStop[],
  thresholdMin: number,
): SupplySummary {
  return {
    stops: stops.length,
    overGap: stops.filter((stop) => overGap(stop, thresholdMin)).length,
    unplaced: stops.filter((stop) => !isPlaced(stop)).length,
    readAgencies: (response?.agencies ?? []).filter((agency) => agency.status === "ok").length,
  };
}

/** [west, south, east, north] around every placed stop, or null with none. */
export function stopBounds(stops: readonly MapStop[]): [number, number, number, number] | null {
  let bounds: [number, number, number, number] | null = null;
  for (const stop of stops) {
    if (!isPlaced(stop)) continue;
    if (!bounds) {
      bounds = [stop.lon, stop.lat, stop.lon, stop.lat];
      continue;
    }
    bounds[0] = Math.min(bounds[0], stop.lon);
    bounds[1] = Math.min(bounds[1], stop.lat);
    bounds[2] = Math.max(bounds[2], stop.lon);
    bounds[3] = Math.max(bounds[3], stop.lat);
  }
  return bounds;
}
