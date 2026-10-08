import type { LngLatBoundsLike, LngLatLike, Map as MLMap } from "maplibre-gl";
import { prefersReducedMotion } from "../../utils/motion";

/**
 * Every camera move on the operations map, in one place.
 *
 * A map that flies, eases and jumps with a different duration and a different
 * curve per call site reads as unrelated maps. These moves are the whole
 * vocabulary: arrive at an agency, frame a route, step in on one trip, pull
 * back to everything, and tilt for the relief layer. They share one easing
 * and two durations so the camera has a recognisable hand.
 */

/** Mirrors `--dur-3` and `--dur-4` in global.css. Duplicated as numbers
 *  because MapLibre animates on its own clock and cannot read a custom
 *  property; the pair must be changed together. */
export const MOTION = { move: 600, reveal: 1200 } as const;

/** `--ease-out`: cubic-bezier(.22, 1, .36, 1). */
const EASE_X1 = 0.22;
const EASE_Y1 = 1;
const EASE_X2 = 0.36;
const EASE_Y2 = 1;

/** Zoom levels the reveal starts back from, so the agency grows into frame. */
const REVEAL_BACKOFF = 0.6;
const REVEAL_PITCH = 30;
const REVEAL_PADDING = 80;
const REVEAL_MAX_ZOOM = 14;

const ROUTE_PADDING = 65;
const ROUTE_MAX_ZOOM = 13;

const FIT_ALL_PADDING = 70;
const FIT_ALL_MAX_ZOOM = 14;

/** Closest a trip inspection ever pulls the camera back to. A trip the
 *  operator is already zoomed past must not be "helpfully" zoomed out. */
const INSPECT_ZOOM = 13;

/** The relief layer's tilt. The one pitched resting state in the app: steep
 *  enough that a column has a visible face, shallow enough that the basemap
 *  still reads as a map rather than a horizon. Never exceeded. */
export const RELIEF_PITCH = 35;

function bezier(t: number, p1: number, p2: number): number {
  const u = 1 - t;
  return 3 * u * u * t * p1 + 3 * u * t * t * p2 + t * t * t;
}

/**
 * `--ease-out` as a MapLibre `easing` function: the CSS curve applies only to
 * animated CSS properties, so the camera needs the same cubic-bezier
 * evaluated numerically. Bisection rather than Newton iteration — the curve's
 * x component has a near-zero derivative at t=0, where Newton is least stable,
 * and a fixed iteration count keeps the result deterministic across runs.
 */
export function easeOutCamera(progress: number): number {
  if (progress <= 0) return 0;
  if (progress >= 1) return 1;
  let low = 0;
  let high = 1;
  let t = progress;
  for (let step = 0; step < 24; step += 1) {
    t = (low + high) / 2;
    if (bezier(t, EASE_X1, EASE_X2) < progress) low = t;
    else high = t;
  }
  return bezier(t, EASE_Y1, EASE_Y2);
}

/** Duration and curve for one move, or an instant cut under reduced motion.
 *  The easing is dropped along with the duration so a zero-length animation
 *  never carries a curve that could be read as intent. */
function timing(duration: number): { duration: number; easing?: (progress: number) => number } {
  return prefersReducedMotion() ? { duration: 0 } : { duration, easing: easeOutCamera };
}

/**
 * First arrival at an agency: the map tilts slightly, sits a little wider than
 * the final frame, and settles over `MOTION.reveal`. Apart from the relief
 * tilt the operator asks for, this is the only pitched move in the app — it
 * happens once per agency, to say "here is the whole network" before anything
 * is read off the map.
 *
 * `restPitch` is where the camera settles — 0 unless the relief layer is on,
 * in which case flattening the map would flatten the columns the operator
 * just turned on.
 */
export function revealAgency(map: MLMap, bounds: LngLatBoundsLike, restPitch: number): void {
  const camera = map.cameraForBounds(bounds, { padding: REVEAL_PADDING, maxZoom: REVEAL_MAX_ZOOM });
  const center = camera?.center;
  const zoom = camera?.zoom;
  if (center == null || zoom == null) {
    map.fitBounds(bounds, { padding: REVEAL_PADDING, maxZoom: REVEAL_MAX_ZOOM, pitch: restPitch, ...timing(MOTION.reveal) });
    return;
  }
  if (prefersReducedMotion()) {
    map.jumpTo({ center, zoom, pitch: restPitch });
    return;
  }
  map.jumpTo({ center, zoom: Math.max(zoom - REVEAL_BACKOFF, 0), pitch: REVEAL_PITCH });
  map.flyTo({ center, zoom, pitch: restPitch, duration: MOTION.reveal, easing: easeOutCamera });
}

// Every framing move names the pitch it ends on. A move without one keeps
// whatever pitch is current, so one that lands during the relief tilt would
// freeze the camera halfway.

/** Frames one route's shape. */
export function focusRoute(map: MLMap, bounds: LngLatBoundsLike, restPitch: number): void {
  map.fitBounds(bounds, { padding: ROUTE_PADDING, maxZoom: ROUTE_MAX_ZOOM, pitch: restPitch, ...timing(MOTION.move) });
}

/** Frames every located trip currently on screen. */
export function fitAll(map: MLMap, bounds: LngLatBoundsLike, restPitch: number): void {
  map.fitBounds(bounds, { padding: FIT_ALL_PADDING, maxZoom: FIT_ALL_MAX_ZOOM, pitch: restPitch, ...timing(MOTION.move) });
}

/** Steps in on one reported position. `zoom` is for callers with their own
 *  target — a cluster expanding by a fixed step rather than to a floor. */
export function inspectTrip(map: MLMap, lngLat: LngLatLike, { zoom, pitch }: { zoom?: number; pitch: number }): void {
  map.easeTo({
    center: lngLat,
    zoom: zoom ?? Math.max(map.getZoom(), INSPECT_ZOOM),
    pitch,
    ...timing(MOTION.move),
  });
}

/** Tilts for the relief layer, or lays the map flat again when it is turned
 *  off. The only way the relief may move the camera.
 *
 *  `easeTo` stops whatever camera move is in flight, so a toggle during a
 *  framing move or a drag would leave the camera partway. The tilt waits for
 *  that move to land instead, and is skipped if the move landed at the target
 *  pitch anyway. Returns a function that drops a tilt still waiting. */
export function reliefPitch(map: MLMap, on: boolean): () => void {
  const pitch = on ? RELIEF_PITCH : 0;
  const tilt = () => {
    if (map.getPitch() !== pitch) map.easeTo({ pitch, ...timing(MOTION.move) });
  };
  if (!map.isMoving()) {
    map.easeTo({ pitch, ...timing(MOTION.move) });
    return () => {};
  }
  map.once("moveend", tilt);
  return () => map.off("moveend", tilt);
}
