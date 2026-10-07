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
export type ReliefCollection = GeoJSON.FeatureCollection<GeoJSON.Polygon, ReliefProps>;
type FillExtrusionPaint = NonNullable<Extract<LayerSpecification, { type: "fill-extrusion" }>["paint"]>;
/** The share of a playback frame's dwell a reading's tween may take. Each
 *  write reloads the source, and while it reloads the map is not "style
 *  loaded", so a tween still running at the next frame would hold back the
 *  playback dots' own update; ending well inside the dwell leaves room for
 *  the last reload to land. */
const RELIEF_DWELL_SHARE = 0.6;

export function reliefHeight(delayMin: number, opts: ReliefOptions = {}): number {
  const { capMin = RELIEF_CAP_MIN, metresPerMin = RELIEF_HEIGHT_M_PER_MIN, baseM = RELIEF_BASE_M } = opts;
  return Math.min(Math.max(delayMin, 0), capMin) * metresPerMin + baseM;
}

/** One square polygon per stop, sized in metres so it is the same footprint
 *  at every latitude, with the extrusion height precomputed as `h` -- the
 *  paint expression stays a plain `["get", "h"]` and the cap/ramp maths
 *  lives here, where it can be tested without a map. */
export function reliefFeatures(points: ReliefPoint[], opts: ReliefOptions = {}): ReliefCollection {
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
 * one the live and playback circles use, so the column and the dot standing
 * on it always agree.
 *
 * Neither property has a paint transition: MapLibre does not ease paint
 * that reads feature data, so height and colour are eased by
 * `tweenFeatures` instead, one `setData` per step.
 */
export function reliefPaint(): FillExtrusionPaint {
  return {
    "fill-extrusion-color": ["step", ["get", "delay_min"], ...severityStepColors()],
    "fill-extrusion-height": ["get", "h"],
    "fill-extrusion-base": 0,
    "fill-extrusion-opacity": 0.92,
  };
}

/** How long a new reading's tween runs: the cross-fade, or during playback
 *  no more than `RELIEF_DWELL_SHARE` of a frame's dwell (`frameDwellMs`). */
export function reliefTweenMs(crossFadeMs: number, frameDwellMs: number | null): number {
  return frameDwellMs == null ? crossFadeMs : Math.min(crossFadeMs, frameDwellMs * RELIEF_DWELL_SHARE);
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

/**
 * One step of the move from the reading on screen to the next one: every
 * stop in `next`, with `h` and `delay_min` run linearly from its value in
 * `prev` by `t` (0..1; the caller applies the easing). A stop that has just
 * started reporting rises from the ground tile, and one that has stopped is
 * dropped at once -- a column shrinking towards a reading that no longer
 * exists would show a delay nobody measured.
 */
export function tweenFeatures(prev: ReliefCollection, next: ReliefCollection, t: number): ReliefCollection {
  const from = new Map(prev.features.map((f) => [f.properties.stop_id, f.properties]));
  return {
    type: "FeatureCollection",
    features: next.features.map((f) => {
      const a = from.get(f.properties.stop_id) ?? { delay_min: 0, h: RELIEF_BASE_M };
      const b = f.properties;
      return {
        ...f,
        properties: { stop_id: b.stop_id, delay_min: a.delay_min + (b.delay_min - a.delay_min) * t, h: a.h + (b.h - a.h) * t },
      };
    }),
  };
}

/** Whether two collections carry the same reading: a re-render that rebuilt
 *  the same points must not restart a tween already running towards them. */
export function sameReliefReading(a: ReliefCollection, b: ReliefCollection): boolean {
  if (a.features.length !== b.features.length) return false;
  return a.features.every((f, i) => {
    const g = b.features[i].properties;
    return f.properties.stop_id === g.stop_id && f.properties.h === g.h && f.properties.delay_min === g.delay_min;
  });
}
