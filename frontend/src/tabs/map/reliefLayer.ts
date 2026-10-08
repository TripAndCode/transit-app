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
/** Narrowest half-width a column draws at, in screen pixels. Below street
 *  zoom the footprint shrinks to a pixel or two and the vehicle dot standing
 *  on it covers it whole. */
const MIN_HALF_SIDE_PX = 4;
const EARTH_CIRCUMFERENCE_M = 40_075_016.686;
/** MapLibre's world is this many pixels wide at zoom 0. */
const WORLD_PX_AT_ZOOM_0 = 512;

export type ReliefPoint = { stop_id: string; lon: number; lat: number; delay_min: number };
type ReliefProps = { stop_id: string; delay_min: number; h: number };
export type ReliefCollection = GeoJSON.FeatureCollection<GeoJSON.Polygon, ReliefProps>;
type FillExtrusionPaint = NonNullable<Extract<LayerSpecification, { type: "fill-extrusion" }>["paint"]>;


export function reliefHeight(delayMin: number): number {
  return Math.min(Math.max(delayMin, 0), RELIEF_CAP_MIN) * RELIEF_HEIGHT_M_PER_MIN + RELIEF_BASE_M;
}

/** How much to enlarge every column at `zoom`: 1 at street zoom, and below it
 *  enough to keep a column `MIN_HALF_SIDE_PX` wide. Footprint and height grow
 *  by the same factor, so a column keeps its shape and the heights keep their
 *  ratios -- height still reads as delay, compared across the map. */
export function reliefScale(zoom: number, lat: number): number {
  const metresPerPx = (EARTH_CIRCUMFERENCE_M * Math.cos((lat * Math.PI) / 180)) / (WORLD_PX_AT_ZOOM_0 * 2 ** zoom);
  return Math.max(1, (MIN_HALF_SIDE_PX * metresPerPx) / RELIEF_HALF_SIDE_M);
}

/** One square polygon per stop, sized in metres so it is the same footprint
 *  at every latitude, with the extrusion height precomputed as `h` -- the
 *  paint expression stays a plain `["get", "h"]` and the cap/ramp maths
 *  lives here, where it can be tested without a map. `scale` enlarges both,
 *  per `reliefScale`. */
export function reliefFeatures(points: ReliefPoint[], scale = 1): ReliefCollection {
  const halfSideM = RELIEF_HALF_SIDE_M * scale;
  const features: GeoJSON.Feature<GeoJSON.Polygon, ReliefProps>[] = [];
  for (const p of points) {
    if (!Number.isFinite(p.lon) || !Number.isFinite(p.lat)) continue;
    const dLat = halfSideM / M_PER_DEG_LAT;
    const dLon = halfSideM / (M_PER_DEG_LON_AT_EQUATOR * Math.cos((p.lat * Math.PI) / 180));
    features.push({
      type: "Feature",
      properties: { stop_id: p.stop_id, delay_min: p.delay_min, h: reliefHeight(p.delay_min) * scale },
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
 * that reads feature data. Height is eased by `tweenFeatures`, one `setData`
 * per step; colour steps through the ramp as the tweened delay crosses each
 * threshold.
 */
export function reliefPaint(): FillExtrusionPaint {
  return {
    "fill-extrusion-color": ["step", ["get", "delay_min"], ...severityStepColors()],
    "fill-extrusion-height": ["get", "h"],
    "fill-extrusion-base": 0,
    "fill-extrusion-opacity": 0.92,
  };
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
