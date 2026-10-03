import type { LayerSpecification } from "maplibre-gl";
import type { LiveTrip, TimelineFrame } from "../../api/types";
import { severityStepColors } from "../../styles/tokens";

export const RELIEF_SOURCE = "relief-columns";
export const RELIEF_LAYER = "relief-columns";

/** Metres of column per minute of delay. The one encoding this layer has:
 *  height is delay and nothing else -- not sample count, not vehicles. */
export const RELIEF_HEIGHT_M_PER_MIN = 55;
/** Delay past this is "severe" at the top of the ramp already, and a column
 *  that kept growing would read as a skyscraper rather than a reading. */
export const RELIEF_CAP_MIN = 5.5;
/** Floor so a punctual stop is still a tile on the ground, not a hole. */
export const RELIEF_BASE_M = 6;
/** Half the square's side. Small enough that neighbouring stops stay apart
 *  at street zoom, large enough to catch light on its faces. */
const RELIEF_HALF_SIDE_M = 20;

const M_PER_DEG_LAT = 110_574;
const M_PER_DEG_LON_AT_EQUATOR = 111_320;

export type ReliefPoint = { stop_id: string; lon: number; lat: number; delay_min: number };
type ReliefOptions = { capMin?: number; metresPerMin?: number; baseM?: number; halfSideM?: number };
type ReliefProps = { stop_id: string; delay_min: number; h: number };
type FillExtrusionPaint = NonNullable<Extract<LayerSpecification, { type: "fill-extrusion" }>["paint"]>;
/** MapLibre accepts `<property>-transition` beside every paint property but
 *  the bundle's types do not declare them; the two this layer sets are
 *  spelled out rather than cast at the call site. */
type ReliefPaint = FillExtrusionPaint & {
  "fill-extrusion-height-transition": { duration: number; delay: number };
  "fill-extrusion-color-transition": { duration: number; delay: number };
};

export function reliefHeight(delayMin: number, opts: ReliefOptions = {}): number {
  const { capMin = RELIEF_CAP_MIN, metresPerMin = RELIEF_HEIGHT_M_PER_MIN, baseM = RELIEF_BASE_M } = opts;
  return Math.min(Math.max(delayMin, 0), capMin) * metresPerMin + baseM;
}

/** One square polygon per stop, sized in metres so it is the same footprint
 *  at every latitude, with the extrusion height precomputed as `h` -- the
 *  paint expression stays a plain `["get", "h"]` and the cap/ramp maths
 *  lives here, where it can be tested without a map. */
export function reliefFeatures(
  points: ReliefPoint[],
  opts: ReliefOptions = {},
): GeoJSON.FeatureCollection<GeoJSON.Polygon, ReliefProps> {
  const halfSideM = opts.halfSideM ?? RELIEF_HALF_SIDE_M;
  const features: GeoJSON.Feature<GeoJSON.Polygon, ReliefProps>[] = [];
  for (const p of points) {
    if (!Number.isFinite(p.lon) || !Number.isFinite(p.lat)) continue;
    const dLat = halfSideM / M_PER_DEG_LAT;
    const dLon = halfSideM / (M_PER_DEG_LON_AT_EQUATOR * Math.cos((p.lat * Math.PI) / 180));
    features.push({
      type: "Feature",
      properties: { stop_id: p.stop_id, delay_min: p.delay_min, h: reliefHeight(p.delay_min, opts) },
      geometry: {
        type: "Polygon",
        coordinates: [[
          [p.lon - dLon, p.lat - dLat],
          [p.lon + dLon, p.lat - dLat],
          [p.lon + dLon, p.lat + dLat],
          [p.lon - dLon, p.lat + dLat],
          [p.lon - dLon, p.lat - dLat],
        ]],
      },
    });
  }
  return { type: "FeatureCollection", features };
}

/**
 * Paint for the column layer. Colour is the shared severity ramp, the same
 * one the live and playback circles use, so switching the relief on changes
 * the mark's shape and never its meaning.
 *
 * `crossFadeMs` is the playback cross-fade, 0 under reduced motion. MapLibre
 * interpolates a `-transition` only between two constant values; both
 * properties here are data-driven, so a new reading re-tessellates the
 * columns at their new height rather than tweening them. The transitions are
 * declared so the layer follows the same duration contract as the playback
 * layer, and drop to 0 together with it.
 */
export function reliefPaint(crossFadeMs: number): ReliefPaint {
  return {
    "fill-extrusion-color": ["step", ["get", "delay_min"], ...severityStepColors()],
    "fill-extrusion-height": ["get", "h"],
    "fill-extrusion-base": 0,
    "fill-extrusion-opacity": 0.92,
    // Lit faces: the gradient is what separates a column from a flat tile
    // when the camera is tilted; it is a shader flag, not extra geometry.
    "fill-extrusion-vertical-gradient": true,
    "fill-extrusion-height-transition": { duration: crossFadeMs, delay: 0 },
    "fill-extrusion-color-transition": { duration: crossFadeMs, delay: 0 },
  } as ReliefPaint;
}

/** One column per stop from the live rows: the mean of every vehicle's
 *  latest reading there, in minutes to match the ramp. */
export function reliefPointsFromLive(rows: LiveTrip[]): ReliefPoint[] {
  const buckets = new Map<string, { lon: number; lat: number; total: number; count: number }>();
  for (const row of rows) {
    if (!row.stop_id || row.stop_lon == null || row.stop_lat == null) continue;
    const b = buckets.get(row.stop_id) ?? { lon: row.stop_lon, lat: row.stop_lat, total: 0, count: 0 };
    b.total += row.dep_delay / 60;
    b.count += 1;
    buckets.set(row.stop_id, b);
  }
  return [...buckets].map(([stop_id, b]) => ({ stop_id, lon: b.lon, lat: b.lat, delay_min: b.total / b.count }));
}

/** A playback frame already carries one averaged point per stop. */
export function reliefPointsFromFrame(frame: TimelineFrame | undefined): ReliefPoint[] {
  return (frame?.points ?? []).map((p) => ({ stop_id: p.stop_id, lon: p.lon, lat: p.lat, delay_min: p.avg_delay_min }));
}
