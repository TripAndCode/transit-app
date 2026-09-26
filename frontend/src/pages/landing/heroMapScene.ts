// Deterministic geometry for the landing hero's fictional harbor city:
// coastline, river, parks, streets, and six routes (rail, tram, bus) whose
// per-section delay profiles drive both the route tint and the stop towers.
// World units are arbitrary: the ground is the y = 0 plane, x runs east and
// z runs north. The numbers are illustrative sample data, not live figures.

import type { VehicleMode } from "./vehicleIcons";

export type Point2 = readonly [number, number];

export type Polyline = {
  points: readonly Point2[];
  length: number;
  /** Point at fraction `u` (0..1, clamped) of the polyline's arc length. */
  at: (u: number) => Point2;
};

export type StationId = "central" | "harbor" | "west" | "eastHill" | "north" | "seaside";
export type DistrictId = "north" | "riverside" | "central" | "port" | "east";

export type MapRoute = {
  id: string;
  mode: VehicleMode;
  path: Polyline;
  /** Average delay in minutes at fraction `u` along the route. */
  delayAt: (u: number) => number;
};

type Station = { id: StationId; x: number; z: number; major: boolean };
type District = { id: DistrictId; x: number; z: number };
export type StopTower = { x: number; z: number; delay: number };

/** The single most-delayed section, which the hero calls out by name. */
type Hotspot = { x: number; z: number; delay: number; weekOverWeek: number };

export type HeroMap = {
  coastline: readonly Point2[];
  riverBanks: readonly Point2[];
  parks: readonly (readonly Point2[])[];
  routes: readonly MapRoute[];
  stations: readonly Station[];
  districts: readonly District[];
  towers: readonly StopTower[];
  tallestTower: StopTower;
  hotspot: Hotspot;
};

/** Half-width of the world the map is drawn over. Wide enough that the
 *  ground never ends inside the frame at any camera pose the timeline uses. */
export const WORLD_EXTENT = 120;

export const coastZ = (x: number): number => -6 + 3 * Math.sin(x * 0.13 + 1) + 1.5 * Math.sin(x * 0.37);
const riverCenterX = (z: number): number => -9 + 4 * Math.sin(z * 0.09 + 0.5);
const RIVER_HALF_WIDTH = 1.1;

const bump = (u: number, center: number, width: number): number => {
  const d = (u - center) / width;
  return Math.exp(-d * d);
};

export function polyline(points: readonly Point2[]): Polyline {
  const cumulative = [0];
  for (let i = 1; i < points.length; i++) {
    const [ax, az] = points[i - 1];
    const [bx, bz] = points[i];
    cumulative.push(cumulative[i - 1] + Math.hypot(bx - ax, bz - az));
  }
  const length = cumulative[cumulative.length - 1];
  const at = (u: number): Point2 => {
    const s = Math.min(1, Math.max(0, u)) * length;
    let i = 1;
    while (i < cumulative.length - 1 && cumulative[i] < s) i++;
    const span = cumulative[i] - cumulative[i - 1] || 1;
    const k = (s - cumulative[i - 1]) / span;
    const [ax, az] = points[i - 1];
    const [bx, bz] = points[i];
    return [ax + (bx - ax) * k, az + (bz - az) * k];
  };
  return { points, length, at };
}

/** Catmull-Rom spline through `control`, so rail lines curve like real track
 *  instead of kinking at every control point. */
function smooth(control: readonly Point2[], stepsPerSpan = 12): Point2[] {
  const out: Point2[] = [];
  for (let i = 0; i < control.length - 1; i++) {
    const p0 = control[Math.max(0, i - 1)];
    const p1 = control[i];
    const p2 = control[i + 1];
    const p3 = control[Math.min(control.length - 1, i + 2)];
    for (let k = 0; k < stepsPerSpan; k++) {
      const t = k / stepsPerSpan;
      const t2 = t * t;
      const t3 = t2 * t;
      const axis = (j: 0 | 1) =>
        0.5 *
        (2 * p1[j] +
          (-p0[j] + p2[j]) * t +
          (2 * p0[j] - 5 * p1[j] + 4 * p2[j] - p3[j]) * t2 +
          (-p0[j] + 3 * p1[j] - 3 * p2[j] + p3[j]) * t3);
      out.push([axis(0), axis(1)]);
    }
  }
  out.push(control[control.length - 1]);
  return out;
}

/** Bus route 12's worst section; the callout and the tallest tower both sit
 *  here so the two acts point at the same place. */
const HOTSPOT_ROUTE_ID = "bus-12";
const HOTSPOT_U = 0.5;
const HOTSPOT_WEEK_OVER_WEEK = 2.1;

function buildRoutes(): MapRoute[] {
  const tramTrack = polyline(
    Array.from({ length: 28 }, (_, i) => {
      const x = -27 + i * 2;
      return [x, coastZ(x) + 3] as Point2;
    }),
  );
  return [
    {
      id: "rail-east-west",
      mode: "train",
      path: polyline(smooth([[-34, 21], [-16, 18], [-4, 14], [6, 12], [18, 16], [34, 25]])),
      delayAt: (u) => 0.4 + 0.6 * bump(u, 0.7, 0.2),
    },
    {
      id: "rail-north-south",
      mode: "train",
      path: polyline(smooth([[-1, 48], [-3, 30], [-4, 14], [-1, 5], [7, 1]])),
      delayAt: () => 0.3,
    },
    { id: "tram-coast", mode: "tram", path: tramTrack, delayAt: (u) => 0.5 + 2.8 * bump(u, 0.68, 0.16) },
    {
      id: HOTSPOT_ROUTE_ID,
      mode: "bus",
      path: polyline([[-22, 3], [-12, 7], [-4, 14], [4, 24], [10, 38]]),
      delayAt: (u) => 0.6 + 6 * bump(u, HOTSPOT_U, 0.17),
    },
    {
      id: "bus-7",
      mode: "bus",
      path: polyline([[22, 2], [14, 8], [6, 12], [0, 22], [-12, 30], [-24, 34]]),
      delayAt: (u) => 0.4 + 4.2 * bump(u, 0.35, 0.15),
    },
    {
      id: "bus-3",
      mode: "bus",
      path: polyline([[-28, 28], [-14, 26], [-3, 30], [12, 28], [28, 33]]),
      delayAt: (u) => 0.3 + 2 * bump(u, 0.6, 0.2),
    },
  ];
}

/** Evenly spaced interior stops per route; rail stops are sparser than bus
 *  stops, as they are on a real network. */
function buildTowers(routes: readonly MapRoute[]): StopTower[] {
  return routes.flatMap((route) => {
    const count = route.mode === "train" ? 5 : 6;
    return Array.from({ length: count - 1 }, (_, i) => {
      const u = (i + 1) / count;
      const [x, z] = route.path.at(u);
      return { x, z, delay: route.delayAt(u) };
    });
  });
}

export function buildHeroMap(): HeroMap {
  const coastline: Point2[] = [];
  for (let x = -WORLD_EXTENT; x <= WORLD_EXTENT; x += 2) coastline.push([x, coastZ(x)]);

  const left: Point2[] = [];
  const right: Point2[] = [];
  for (let z = coastZ(riverCenterX(-6)) - 1; z <= WORLD_EXTENT; z += 2) {
    left.push([riverCenterX(z) - RIVER_HALF_WIDTH, z]);
    right.push([riverCenterX(z) + RIVER_HALF_WIDTH, z]);
  }

  const routes = buildRoutes();
  const towers = buildTowers(routes);
  const tallestTower = towers.reduce((a, b) => (b.delay > a.delay ? b : a));
  const hotspotRoute = routes.find((r) => r.id === HOTSPOT_ROUTE_ID)!;
  const [hx, hz] = hotspotRoute.path.at(HOTSPOT_U);

  return {
    coastline,
    riverBanks: [...left, ...right.reverse()],
    parks: [
      [[8, 30], [16, 30], [17, 36], [9, 37]],
      [[-20, 38], [-13, 37], [-12, 43], [-21, 44]],
    ],
    routes,
    stations: [
      { id: "central", x: -4, z: 14, major: true },
      { id: "harbor", x: 6, z: 12, major: false },
      { id: "west", x: -16, z: 18, major: false },
      { id: "eastHill", x: 18, z: 16, major: false },
      { id: "north", x: -3, z: 30, major: false },
      { id: "seaside", x: 7, z: 1, major: false },
    ],
    districts: [
      { id: "north", x: -2, z: 40 },
      { id: "riverside", x: -22, z: 12 },
      { id: "central", x: 4, z: 19 },
      { id: "port", x: 16, z: -1 },
      { id: "east", x: 24, z: 22 },
    ],
    towers,
    tallestTower,
    hotspot: { x: hx, z: hz, delay: hotspotRoute.delayAt(HOTSPOT_U), weekOverWeek: HOTSPOT_WEEK_OVER_WEEK },
  };
}
