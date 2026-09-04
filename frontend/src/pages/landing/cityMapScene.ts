// Pure, deterministic geometry for the landing hero's schematic city map.
// Everything here operates in normalized [0,1] x [0,1] space so it's
// independent of the actual canvas pixel size (CityMapHero.tsx multiplies by
// the live width/height at draw time) and fully unit-testable without a
// canvas context.

import type { VehicleMode } from "./vehicleIcons";

export type Rect = { x: number; y: number; w: number; h: number };
export type Point = { x: number; y: number };

/** One metro-style route: a bent (right-angle) polyline between station
 *  points. `colorVar` names the real theme token this route's color carries
 *  semantic meaning for -- `--accent` (on-time) or `--color-warning`
 *  (elevated delay) -- resolved to an actual color string at draw time, not
 *  baked in here. `vehicleMode` is fixed per route (never mixed across the
 *  vehicles running on the same line), mirroring how a real transit route is
 *  consistently "the bus line" or "the train line." */
export type RouteLine = {
  id: string;
  colorVar: "--accent" | "--color-warning";
  vehicleMode: VehicleMode;
  points: Point[];
};

export type CityScene = { blocks: Rect[]; park: Rect; river: Point[]; routes: RouteLine[] };

/** Small deterministic PRNG (mulberry32) so the block layout is stable
 *  across reloads and identical in tests -- "procedural" here means
 *  algorithmically generated, not randomized per visit. */
function mulberry32(seed: number): () => number {
  let state = seed;
  return function next() {
    state = (state + 0x6d2b79f5) | 0;
    let t = state;
    t = Math.imul(t ^ (t >>> 15), t | 1);
    t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

// Arbitrary fixed seed -- picked once so the hero's block layout doesn't
// visibly shuffle on every reload, not derived from any real data.
const SCENE_SEED = 20260904;

const GRID_COLS = 8;
const GRID_ROWS = 5;

/** Builds the hero's schematic city: a grid of blocks with a park patch and
 *  a river carved out, plus two fixed metro-style routes overlaid on top.
 *  Deterministic (same seed every call) so it can be asserted against in
 *  tests and doesn't jump around between renders. */
export function buildCityScene(): CityScene {
  const rand = mulberry32(SCENE_SEED);
  const cellW = 1 / GRID_COLS;
  const cellH = 1 / GRID_ROWS;
  const parkCol = 3;
  const parkRow = 2;
  const riverCol = 6;

  const blocks: Rect[] = [];
  for (let row = 0; row < GRID_ROWS; row++) {
    for (let col = 0; col < GRID_COLS; col++) {
      const isPark = row === parkRow && (col === parkCol || col === parkCol + 1);
      const isRiver = col === riverCol && row >= 1 && row <= 3;
      if (isPark || isRiver) continue;
      const cx = col * cellW;
      const cy = row * cellH;
      const margin = 0.012 + rand() * 0.01;
      // Shrinks each block's footprint within its cell by a varying amount
      // so the grid reads as hand-drawn city blocks, not a uniform lattice.
      const shrink = 0.08 + rand() * 0.18;
      blocks.push({
        x: cx + margin,
        y: cy + margin,
        w: cellW - margin * 2 - shrink * cellW,
        h: cellH - margin * 2 - shrink * cellH,
      });
    }
  }

  const park: Rect = { x: parkCol * cellW, y: parkRow * cellH, w: 2 * cellW, h: cellH };
  const river: Point[] = [
    { x: riverCol * cellW + 0.02, y: 0 },
    { x: riverCol * cellW + 0.06, y: 0.35 },
    { x: riverCol * cellW - 0.01, y: 0.7 },
    { x: riverCol * cellW + 0.04, y: 1 },
  ];

  // Hand-placed waypoints (not derived from the block grid) so the two
  // routes read as an overlaid transit map rather than literally tracing
  // block edges.
  const routes: RouteLine[] = [
    {
      id: "on-time",
      colorVar: "--accent",
      vehicleMode: "bus",
      points: [
        { x: 0.08, y: 0.82 },
        { x: 0.08, y: 0.55 },
        { x: 0.34, y: 0.55 },
        { x: 0.34, y: 0.3 },
        { x: 0.62, y: 0.3 },
        { x: 0.62, y: 0.12 },
        { x: 0.9, y: 0.12 },
      ],
    },
    {
      id: "delayed",
      colorVar: "--color-warning",
      vehicleMode: "train",
      points: [
        { x: 0.14, y: 0.92 },
        { x: 0.46, y: 0.92 },
        { x: 0.46, y: 0.68 },
        { x: 0.7, y: 0.68 },
        { x: 0.7, y: 0.42 },
        { x: 0.93, y: 0.42 },
      ],
    },
  ];

  return { blocks, park, river, routes };
}

function dist(a: Point, b: Point): number {
  return Math.hypot(b.x - a.x, b.y - a.y);
}

/** Total length of a route's polyline, in normalized units. */
export function routeLength(route: RouteLine): number {
  let total = 0;
  for (let i = 1; i < route.points.length; i++) {
    total += dist(route.points[i - 1], route.points[i]);
  }
  return total;
}

export type VehiclePose = Point & { angleRad: number };

/** Position + heading at fractional distance `t` along a route's bent
 *  polyline, linearly interpolating within whichever segment `t` falls in.
 *  `t` wraps modulo 1 rather than clamping, so a looping vehicle can be
 *  advanced with an ever-increasing `t` and this always resolves to a point
 *  on the route. */
export function poseAtT(route: RouteLine, t: number): VehiclePose {
  const total = routeLength(route);
  if (total === 0 || route.points.length < 2) {
    const p = route.points[0] ?? { x: 0, y: 0 };
    return { x: p.x, y: p.y, angleRad: 0 };
  }
  const wrapped = ((t % 1) + 1) % 1;
  let remaining = wrapped * total;
  for (let i = 1; i < route.points.length; i++) {
    const a = route.points[i - 1];
    const b = route.points[i];
    const segLen = dist(a, b);
    if (remaining <= segLen || i === route.points.length - 1) {
      const ratio = segLen === 0 ? 0 : Math.min(remaining / segLen, 1);
      return {
        x: a.x + (b.x - a.x) * ratio,
        y: a.y + (b.y - a.y) * ratio,
        angleRad: Math.atan2(b.y - a.y, b.x - a.x),
      };
    }
    remaining -= segLen;
  }
  const last = route.points[route.points.length - 1];
  return { x: last.x, y: last.y, angleRad: 0 };
}

export type Vehicle = { routeId: string; t: number; speedPerMs: number };

/** Two vehicles per route, phase-offset by half a lap so they never overlap;
 *  a small per-route speed difference keeps their relative spacing from
 *  ever fully re-syncing. */
export function buildVehicles(routes: RouteLine[]): Vehicle[] {
  const vehicles: Vehicle[] = [];
  routes.forEach((route, routeIndex) => {
    const speedPerMs = 0.00006 + routeIndex * 0.000012;
    vehicles.push({ routeId: route.id, t: 0, speedPerMs });
    vehicles.push({ routeId: route.id, t: 0.5, speedPerMs });
  });
  return vehicles;
}

/** Advances a vehicle's position along its route by `dtMs` milliseconds.
 *  Pure -- returns a new vehicle rather than mutating, so the caller (a rAF
 *  loop writing into a ref) controls when/whether state actually updates. */
export function advanceVehicle(vehicle: Vehicle, dtMs: number): Vehicle {
  return { ...vehicle, t: vehicle.t + vehicle.speedPerMs * dtMs };
}
