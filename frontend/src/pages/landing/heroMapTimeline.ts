// The hero's scripted sequence as a pure function of elapsed seconds:
// live operations (trip dots, the queue panel, one refresh) → the queue's
// top trip is opened (route gradient, reported-stop trail, which flies into
// the trip panel's chart) → day playback (the rail's hours fly into the
// hourly-deterioration bars, which then light with the clock) → the period
// overview → back to the queue. Everything that only belongs to the first
// play (fly-in, dots appearing, the refresh) settles before LOOP_START, and
// the last frame matches the LOOP_START frame, so the loop has no seam.

import { expoInOut, lerp, mixCamera, segment, type Camera } from "./heroMapMath";

export const DURATION = 18.75;
/** Where every later loop resumes; must come after all first-play motion. */
export const LOOP_START = 4.0;
/** One beat at 128 BPM; staggers are multiples of it so entrances feel timed. */
const BEAT = 60 / 128;

const FLY_IN: Camera = { x: 2, y: 34, z: -24, yaw: 0, pitch: 0.95, focal: 1.2 };
const NETWORK: Camera = { x: 2, y: 60, z: 16, yaw: 0, pitch: Math.PI / 2 - 1e-4, focal: 1.2 };
const ROUTE_CLOSE: Camera = { x: 3, y: 60, z: 18, yaw: 0, pitch: Math.PI / 2 - 1e-4, focal: 1.6 };

type PanelKind = "queue" | "trip" | "hourly" | "overview";
/** A panel section on screen: its entrance/exit progress and the seconds
 *  since it began entering (for counters and staggered rows inside it). */
export type PanelSection = { kind: PanelKind; enter: number; exit: number; since: number };
export type CaptionIndex = 0 | 1 | 2 | 3;

export type HeroFrame = {
  t: number;
  camera: Camera;
  /** 0 = whole network, 1 = close on the selected route. */
  zoom: number;
  tripAppear: (id: number) => number;
  tripsAtNextStop: boolean;
  /** True for the instant between refresh positions, when dots are hidden. */
  tripsBlinking: boolean;
  /** Pop-in scale progress after the jump (1 = settled). */
  tripPop: number;
  tripsAlpha: number;
  tripSelected: boolean;
  refreshChip: number;
  routeReveal: number;
  trailReveal: number;
  /** 0..1: the trail's points flying from the map into the trip chart. */
  trailMorph: number;
  playback: { stopsAlpha: number; railAlpha: number; hour: number };
  /** 0..1: copies of the rail's hour segments flying into the hourly bars. */
  hourMorph: number;
  panelEnter: number;
  sections: PanelSection[];
  queueRipple: number;
  tripNote: number;
  peakLabel: number;
  caption: { index: CaptionIndex; enter: number; exit: number } | null;
};

const SECTION_WINDOWS: readonly [PanelKind, number, number][] = [
  ["queue", 1.9, 5.4],
  ["trip", 5.4, 9.7],
  ["hourly", 9.7, 13.3],
  ["overview", 13.3, 17.0],
  ["queue", 17.0, Infinity],
];
const CAPTION_WINDOWS: readonly [CaptionIndex, number, number][] = [
  [0, 1.8, 4.8],
  [1, 5.0, 9.4],
  [2, 9.8, 13.2],
  [3, 13.4, 16.9],
  [0, 17.0, Infinity],
];
const ENTER = 0.45;
const EXIT = 0.3;

/** An open-ended window (`tOut` = Infinity) never starts exiting. */
function windowState(t: number, tIn: number, tOut: number) {
  return { enter: segment(t, tIn, tIn + ENTER), exit: Number.isFinite(tOut) ? segment(t, tOut - EXIT, tOut) : 0 };
}

function cameraAt(t: number): Camera {
  const landed = mixCamera(FLY_IN, NETWORK, expoInOut(segment(t, 0, 1.6)));
  return mixCamera(landed, ROUTE_CLOSE, zoomAt(t));
}

function zoomAt(t: number): number {
  return expoInOut(segment(t, 4.9, 5.7)) * (1 - expoInOut(segment(t, 9.3, 10.0)));
}

export function frameAt(t: number): HeroFrame {
  const zoom = zoomAt(t);
  const playing = t > 9.7 && t < 13.3;
  const sections: PanelSection[] = [];
  for (const [kind, tIn, tOut] of SECTION_WINDOWS) {
    const { enter, exit } = windowState(t, tIn, tOut);
    if (enter > 0 && exit < 1) sections.push({ kind, enter, exit, since: t - tIn });
  }
  let caption: HeroFrame["caption"] = null;
  for (const [index, tIn, tOut] of CAPTION_WINDOWS) {
    const { enter, exit } = windowState(t, tIn, tOut);
    if (enter > 0 && exit < 1) caption = { index, enter, exit };
  }
  return {
    t,
    camera: cameraAt(t),
    zoom,
    tripAppear: (id) => segment(t, 1.6 + (id % 9) * (BEAT / 3), 1.9 + (id % 9) * (BEAT / 3)),
    tripsAtNextStop: t >= 3.5,
    tripsBlinking: t >= 3.3 && t < 3.5,
    tripPop: t < 3.5 ? 1 : segment(t, 3.5, 3.75),
    tripsAlpha: playing ? 1 - segment(t, 9.7, 10.0) + segment(t, 13.0, 13.3) : 1,
    tripSelected: t > 5.3 && t < 9.4,
    refreshChip: segment(t, 3.1, 3.3) * (1 - segment(t, 3.7, 3.95)),
    routeReveal: segment(t, 5.3, 6.0) * (1 - segment(t, 9.2, 9.6)),
    trailReveal: segment(t, 5.8, 6.4) * (1 - segment(t, 9.2, 9.5)),
    trailMorph: segment(t, 6.7, 7.9),
    playback: {
      stopsAlpha: segment(t, 9.7, 10.0) * (1 - segment(t, 13.0, 13.3)),
      railAlpha: segment(t, 9.8, 10.1) * (1 - segment(t, 13.0, 13.3)),
      hour: lerp(5, 24, segment(t, 10.0, 13.0)),
    },
    hourMorph: segment(t, 10.0, 10.9),
    panelEnter: expoInOut(segment(t, 1.2, 1.9)),
    sections,
    queueRipple: t > 4.5 && t < 5.1 ? segment(t, 4.5, 5.1) : 0,
    tripNote: segment(t, 8.0, 8.3),
    peakLabel: segment(t, 11.0, 11.4),
    caption,
  };
}
