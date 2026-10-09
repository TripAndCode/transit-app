import type { LiveTripProgressResponse } from "../../api/types";

export const PEARL_SOURCE = "pearl-highlight";
export const PEARL_LAYER = "pearl-highlight";
/** The shimmer's fade-in behind its peak, as a fraction of the line; half as
 *  much fades out ahead, so the lit span is 1.5x this. A highlight, not a
 *  sprite. */
export const PEARL_WIDTH = 0.06;
/** One traversal of the reported line. Slow on purpose -- a pearl that
 *  raced along the route would read as a vehicle position, and there is
 *  no GPS behind it. */
export const PEARL_CYCLE_MS = 12_500;
/** White in both themes: it sits on the accent-coloured progress line. */
const PEARL_RGB = "255,255,255";

type LineGradient = (string | number | unknown[])[];

/** Elapsed loop time → position along the line, 0..1, wrapping each cycle. */
export function pearlPhase(elapsedMs: number, cycleMs: number = PEARL_CYCLE_MS): number {
  const pos = ((elapsedMs % cycleMs) + cycleMs) % cycleMs;
  return pos / cycleMs;
}

/** A `line-gradient` that is transparent everywhere except a soft window
 *  around `t`: brightest at `t`, fading in over `w` behind it and out over
 *  `w/2` ahead. Stops are clamped to [0, 1] and kept strictly increasing,
 *  which MapLibre's `interpolate` requires. `t < 0` yields a fully
 *  transparent gradient -- the "off" paint. */
export function pearlGradient(t: number, w: number = PEARL_WIDTH, rgb: string = PEARL_RGB): LineGradient {
  const clear = `rgba(${rgb},0)`;
  const expr: LineGradient = ["interpolate", ["linear"], ["line-progress"]];
  if (t < 0) return [...expr, 0, clear, 1, clear];
  const peak = `rgba(${rgb},.95)`;
  const stops: [number, string][] = [
    [Math.max(0, t - w), clear],
    [Math.min(t, 1), peak],
    [Math.min(1, t + w / 2), clear],
  ];
  if (stops[0][0] > 0) stops.unshift([0, clear]);
  if (stops[stops.length - 1][0] < 1) stops.push([1, clear]);
  // At t = 0 or t = 1 the peak lands on a clamped edge stop; one stop per
  // position survives, and it is the peak, so the highlight never blinks out
  // on the boundary frame.
  let last = -1;
  for (const [pos, color] of stops) {
    if (pos <= last) {
      if (color === peak) expr[expr.length - 1] = peak;
      continue;
    }
    expr.push(pos, color);
    last = pos;
  }
  return expr;
}

/** The segments the feed actually reported for the selected trip, in stop
 *  order -- the only geometry the pearl is allowed to move along. Fewer than
 *  two located stops is not a segment, so there is nothing to light. */
export function pearlLine(progress: LiveTripProgressResponse | undefined): GeoJSON.Feature<GeoJSON.LineString> | null {
  const coords = [...(progress?.stops ?? [])]
    .sort((a, b) => a.stop_sequence - b.stop_sequence)
    .filter((s) => s.stop_lon != null && s.stop_lat != null)
    .map((s) => [s.stop_lon!, s.stop_lat!]);
  if (coords.length < 2) return null;
  return { type: "Feature", properties: {}, geometry: { type: "LineString", coordinates: coords } };
}
