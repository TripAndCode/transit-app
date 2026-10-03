import type { TimelineFrame } from "../../api/types";

/** Wall-clock time one frame is held at 1x. Slow enough that a hub's colour
 *  registers before it changes, fast enough that a whole service day plays in
 *  well under a minute. Speed divides it. */
export const FRAME_MS = 900;

/** How long the layer takes to ease between two frames' opacities. Deliberately
 *  shorter than FRAME_MS so a frame is fully itself for part of its turn rather
 *  than permanently mid-dissolve. */
export const CROSS_FADE_MS = 600;

/** Opacity by feature age: the frame being played, then the two behind it.
 *  The ghosts are a trail, not data to read — weak enough that a reader's eye
 *  stays on the current frame and only picks up the direction of travel. */
export const GHOST_OPACITY = [1, 0.25, 0.1] as const;

/** Number of frames drawn at once, current frame included — one per entry in
 *  GHOST_OPACITY, so the trail's depth and its weights can't drift apart. */
const GHOST_DEPTH = GHOST_OPACITY.length;

/**
 * Which frame is showing after `elapsedMs` of continuous playback.
 *
 * Pure, and the only place playback time becomes a frame number: the rail's
 * timer, its scrubber and the map layer all read the same index, so they can
 * never disagree about what is on screen. Loops rather than stopping at the
 * end — a day-playback that parks on 23:00 reads as broken, and the loop makes
 * the shape of the whole day comparable across repeats.
 */
export function frameIndexAt(elapsedMs: number, frameCount: number, speed: number): number {
  if (frameCount <= 0) return 0;
  const advanced = Math.floor((elapsedMs * speed) / FRAME_MS);
  return ((advanced % frameCount) + frameCount) % frameCount;
}

/** One keyboard/button step, wrapping at both ends. */
export function stepFrameIndex(index: number, delta: number, frameCount: number): number {
  if (frameCount <= 0) return 0;
  return ((index + delta) % frameCount + frameCount) % frameCount;
}

/** Clamp a scrubbed or restored index into the current frame list. */
export function clampFrameIndex(index: number, frameCount: number): number {
  if (frameCount <= 0) return 0;
  return Math.min(Math.max(index, 0), frameCount - 1);
}

type PointFeature = GeoJSON.Feature<GeoJSON.Point, { stop_id: string; delay_min: number; age: number }>;

/**
 * The GeoJSON the `timeline` source carries for one playhead position: the
 * current frame plus up to two earlier ones, each feature tagged with its
 * `age` so a single data-driven paint expression can weight them.
 *
 * Ghosts are emitted oldest-first: within one layer MapLibre paints in feature
 * order, so the current frame has to come last or its own trail would cover it.
 */
export function timelineFeatures(
  frames: TimelineFrame[],
  index: number,
): GeoJSON.FeatureCollection<GeoJSON.Point> {
  const features: PointFeature[] = [];
  if (index >= 0 && index < frames.length) {
    for (let age = GHOST_DEPTH - 1; age >= 0; age -= 1) {
      const frame = frames[index - age];
      if (!frame) continue;
      for (const point of frame.points) {
        features.push({
          type: "Feature",
          geometry: { type: "Point", coordinates: [point.lon, point.lat] },
          properties: { stop_id: point.stop_id, delay_min: point.avg_delay_min, age },
        });
      }
    }
  }
  return { type: "FeatureCollection", features };
}
