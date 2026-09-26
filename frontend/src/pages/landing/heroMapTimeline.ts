// The hero's scripted sequence as a pure function of elapsed seconds, in
// three acts: descend while routes draw on and vehicles run; tint routes by
// per-section delay and call out the worst section; grow a tower at every
// stop. The exact windows are the `segment()` calls in `frameAt`. Afterwards
// the scene holds in a slow orbit rather than looping, since a hard loop
// would replay the descent every few seconds under the page's headline.

import {
  cameraAt,
  easeInOutCubic,
  easeOutCubic,
  segment,
  type Camera,
  type CameraKeyframe,
} from "./heroMapMath";

/** Where the scripted camera path ends and the idle orbit takes over. It is
 *  also the still drawn under reduced motion: towers grown, labels shown,
 *  the last act's caption still up. */
export const SEQUENCE_END = 11.6;

const ORBIT_PIVOT = { x: 2, z: 14 };
const ORBIT_RADIUS = 42;
const ORBIT_HEIGHT = 33;
const ORBIT_START_ANGLE = -0.42;
const ORBIT_SWING = 0.35;
/** Radians per second of the idle swing's phase; one full swing takes well
 *  over a minute, slow enough to read as ambient rather than as motion. */
const ORBIT_RATE = 0.08;

function orbitCamera(angle: number): Camera {
  return {
    x: ORBIT_PIVOT.x - ORBIT_RADIUS * Math.sin(angle),
    y: ORBIT_HEIGHT,
    z: ORBIT_PIVOT.z - ORBIT_RADIUS * Math.cos(angle),
    yaw: angle,
    pitch: 0.74,
    focal: 1.1,
  };
}

const CAMERA_KEYS: readonly CameraKeyframe[] = [
  { t: 0, x: 2, y: 64, z: -46, yaw: -0.05, pitch: 0.78, focal: 1.1 },
  { t: 4.4, x: -1, y: 32, z: -16, yaw: 0.12, pitch: 0.82, focal: 1.1 },
  { t: 7.6, x: -6, y: 17, z: -3, yaw: 0.3, pitch: 0.7, focal: 1.1 },
  { t: SEQUENCE_END, ...orbitCamera(ORBIT_START_ANGLE) },
];

export function cameraForTime(t: number): Camera {
  if (t <= SEQUENCE_END) return cameraAt(t, CAMERA_KEYS);
  // Starts at zero velocity, matching the eased arrival of the last keyframe.
  const swing = (1 - Math.cos((t - SEQUENCE_END) * ORBIT_RATE)) / 2;
  return orbitCamera(ORBIT_START_ANGLE - ORBIT_SWING * swing);
}

export type CaptionIndex = 0 | 1 | 2;

export type HeroFrame = {
  t: number;
  camera: Camera;
  mapReveal: number;
  /** Draw-on progress (0..1) per route, in `HeroMap.routes` order. */
  routeProgress: (routeIndex: number) => number;
  /** How far routes have shifted from their base color to the delay ramp. */
  delayTint: number;
  pulseAlpha: number;
  vehicleAlpha: number;
  stationAlpha: number;
  stationLabels: boolean;
  calloutAlpha: number;
  towerGrowth: (towerIndex: number) => number;
  towerLabelAlpha: number;
  legendAlpha: number;
  caption: { index: CaptionIndex; alpha: number } | null;
};

const CAPTION_WINDOWS: readonly [number, number][] = [
  [1.2, 4.2],
  [4.8, 8.2],
  [9.2, SEQUENCE_END + 1],
];

function captionAt(t: number): HeroFrame["caption"] {
  for (let i = 0; i < CAPTION_WINDOWS.length; i++) {
    const [start, end] = CAPTION_WINDOWS[i];
    const alpha = segment(t, start, start + 0.6) * (1 - segment(t, end - 0.4, end));
    if (alpha > 0) return { index: i as CaptionIndex, alpha };
  }
  return null;
}

export function frameAt(t: number): HeroFrame {
  // Act 3 dims the act-1 layers so the towers read as the subject.
  const towerPhase = segment(t, 8.2, 9);
  return {
    t,
    camera: cameraForTime(t),
    mapReveal: segment(t, 0, 1.2),
    routeProgress: (i) => easeInOutCubic(segment(t, 0.8 + i * 0.3, 3.6 + i * 0.3)),
    delayTint: easeOutCubic(segment(t, 4.4, 7.4)),
    pulseAlpha: segment(t, 3, 3.6) * (1 - 0.7 * towerPhase),
    vehicleAlpha: segment(t, 3.4, 4) * (1 - 0.6 * towerPhase),
    stationAlpha: segment(t, 2, 3) * (1 - 0.55 * towerPhase),
    stationLabels: t < 9.5,
    calloutAlpha: segment(t, 6.4, 7) * (1 - segment(t, 8.4, 9)),
    towerGrowth: (i) => {
      const start = 8.3 + (i % 7) * 0.12;
      return easeOutCubic(segment(t, start, start + 2.5));
    },
    towerLabelAlpha: segment(t, 10.6, 11.3),
    legendAlpha: segment(t, 5, 6),
    caption: captionAt(t),
  };
}
