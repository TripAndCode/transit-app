// Deterministic geometry and sample figures for the landing hero's fictional
// city. Every element stands for something the signed-in app really shows:
// trip dots at their latest reported stop, one selected route's per-stop
// average delay, a selected trip's reported-stop history, the day-playback
// hourly means, and the period-overview numbers. World units are arbitrary:
// the ground is the y = 0 plane, x runs east and z runs north.

export type Point2 = readonly [number, number];

type Polyline = {
  points: readonly Point2[];
  /** Point at fraction `u` (0..1, clamped) of the polyline's arc length. */
  at: (u: number) => Point2;
};

type VehicleMode = "bus" | "train" | "tram";
export type RouteKey = "rapid" | "local" | "tram3" | "bus12" | "bus7" | "bus3";
export type StopKey = "konan" | "shiyakusho" | "central" | "honmachi" | "higashidai" | "minatomachi";

/** A trip dot: sits at its latest reported stop and jumps to `nextU` when
 *  the feed refreshes — the app places trips at stops, it does not glide
 *  them along the line. */
export type Trip = { id: number; route: number; u: number; nextU: number; delay: number; time: string };

export const coastZ = (x: number): number => -6 + 3 * Math.sin(x * 0.13 + 1) + 1.5 * Math.sin(x * 0.37);
const riverCenterX = (z: number): number => -9 + 4 * Math.sin(z * 0.09 + 0.5);
const RIVER_HALF_WIDTH = 1.2;
/** Far enough that the ground never ends inside the frame at any camera pose. */
const FAR = 90;

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
  return { points, at };
}

/** Catmull-Rom spline through `control`, so rail lines curve like track. */
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

const bump = (u: number, center: number, width: number): number => {
  const d = (u - center) / width;
  return Math.exp(-d * d);
};

const tramTrack: Point2[] = Array.from({ length: 28 }, (_, i) => {
  const x = -27 + i * 2;
  return [x, coastZ(x) + 3];
});

export const ROUTES: readonly { key: RouteKey; mode: VehicleMode; path: Polyline }[] = [
  { key: "rapid", mode: "train", path: polyline(smooth([[-40, 22], [-16, 18], [-4, 14], [6, 12], [18, 16], [40, 26]])) },
  { key: "local", mode: "train", path: polyline(smooth([[-1, 50], [-3, 30], [-4, 14], [-1, 5], [7, 1]])) },
  { key: "tram3", mode: "tram", path: polyline(tramTrack) },
  { key: "bus12", mode: "bus", path: polyline([[-22, 3], [-12, 7], [-4, 14], [4, 24], [10, 38]]) },
  { key: "bus7", mode: "bus", path: polyline([[22, 2], [14, 8], [6, 12], [0, 22], [-12, 30], [-24, 34]]) },
  { key: "bus3", mode: "bus", path: polyline([[-28, 28], [-14, 26], [-3, 30], [12, 28], [28, 33]]) },
];

/** The route the hero selects; its per-stop average delay drives the route's
 *  gradient, spaced evenly by stop order as the app draws it. */
export const SELECTED_ROUTE = ROUTES.findIndex((r) => r.key === "bus12");
export const SELECTED_ROUTE_STOP_AVG: readonly number[] = Array.from({ length: 7 }, (_, i) => 0.6 + 6 * bump(i / 6, 0.5, 0.17));

/** Distance, as a fraction of each route, from one stop to the next. */
const STOP_SPACING: readonly number[] = [1 / 7, 1 / 7, 1 / 7, 1 / 6, 1 / 7, 1 / 7];

export const TRIPS: readonly Trip[] = (
  [
    [0, 2 / 7, 0.4, "08:10"], [0, 4 / 7, 0.8, "08:11"], [0, 5 / 7, 1.9, "08:12"], [0, 6 / 7, 0.6, "08:09"],
    [1, 1 / 7, 0.2, "08:12"], [1, 4 / 7, 0.5, "08:10"],
    [2, 2 / 7, 0.7, "08:11"], [2, 5 / 7, 3.4, "08:12"], [2, 5.4 / 7, 2.6, "08:12"],
    [3, 1 / 6, 1.2, "08:08"], [3, 3 / 6, 6.0, "08:12"], [3, 4 / 6, 3.1, "08:11"], [3, 5 / 6, 2.1, "08:10"],
    [4, 2 / 7, 4.1, "08:12"], [4, 5 / 7, 1.0, "08:09"],
    [5, 2 / 7, 0.5, "08:11"], [5, 4 / 7, 1.7, "08:12"],
  ] as const
).map(([route, u, delay, time], id) => ({ id, route, u, nextU: Math.min(1, u + STOP_SPACING[route]), delay, time }));

/** The trip the hero opens: route 12 at 本町, six minutes late — the same
 *  trip heads the queue list, is ringed on the map, and fills the trip panel. */
export const SELECTED_TRIP = TRIPS.find((tp) => tp.route === SELECTED_ROUTE && Math.abs(tp.u - 0.5) < 1e-9)!;

/** The selected trip's reported stops, oldest first; its trail on the map
 *  and the trip panel's per-stop chart are both drawn from this. */
export const TRIP_HISTORY: readonly { stop: StopKey; u: number; delay: number }[] = [
  { stop: "konan", u: 0, delay: 0 },
  { stop: "shiyakusho", u: 1 / 6, delay: 1 },
  { stop: "central", u: 2 / 6, delay: 3 },
  { stop: "honmachi", u: 3 / 6, delay: SELECTED_TRIP.delay },
];

/** Mean delay per hour from 05:00: the playback rail's colors and, for the
 *  same hours, the hourly-deterioration bars. */
export const HOURLY_MEANS: readonly number[] = [0.8, 1.2, 2.6, 4.8, 5.6, 3.4, 2.2, 1.6, 1.4, 1.5, 1.7, 2.3, 3.6, 4.6, 3.9, 2.4, 1.6, 1.1, 0.9];
export const FIRST_SERVICE_HOUR = 5;
/** All 24 hours for the bars; the small hours before service are quiet. */
export const HOURLY_24: readonly number[] = [0.3, 0.2, 0.2, 0.2, 0.4, ...HOURLY_MEANS];
export const PEAK_HOUR = HOURLY_24.indexOf(Math.max(...HOURLY_24));

export const KPI = { observed: 1284, delayedFivePlus: 37, onTimePct: 91 } as const;

export const QUEUE_ROWS: readonly { route: RouteKey; stop: StopKey; delay: number }[] = [
  { route: "bus12", stop: "honmachi", delay: SELECTED_TRIP.delay },
  { route: "bus7", stop: "higashidai", delay: 4 },
  { route: "tram3", stop: "minatomachi", delay: 3 },
];

export const OVERVIEW = {
  networkAvg: 2.4,
  changeVsPrevious: -0.3,
  delayedRoutes: 12,
  totalRoutes: 48,
  trend: [3.1, 2.9, 3.2, 2.8, 2.7, 2.9, 2.6, 2.5, 2.7, 2.4, 2.5, 2.3, 2.4],
  routesToCheck: [
    { route: "bus12", delay: 6.6 },
    { route: "bus7", delay: 4.6 },
    { route: "tram3", delay: 3.3 },
    { route: "rapid", delay: 1.9 },
  ],
} as const satisfies {
  networkAvg: number;
  changeVsPrevious: number;
  delayedRoutes: number;
  totalRoutes: number;
  trend: readonly number[];
  routesToCheck: readonly { route: RouteKey; delay: number }[];
};

function buildBasemap() {
  const coastline: Point2[] = [];
  for (let x = -FAR; x <= FAR; x += 2) coastline.push([x, coastZ(x)]);
  const left: Point2[] = [];
  const right: Point2[] = [];
  for (let z = coastZ(riverCenterX(-6)) - 2; z <= FAR; z += 2) {
    left.push([riverCenterX(z) - RIVER_HALF_WIDTH, z]);
    right.push([riverCenterX(z) + RIVER_HALF_WIDTH, z]);
  }
  const sea: Point2[] = [...coastline, [FAR, -FAR], [-FAR, -FAR]];
  const parks: Point2[][] = [
    [[8, 30], [16, 30], [17, 36], [9, 37]],
    [[-20, 38], [-13, 37], [-12, 43], [-21, 44]],
  ];
  const roads: Point2[][] = [
    [[-70, 8], [70, 4]], [[-70, 20], [70, 23]], [[-70, 32], [70, 30]], [[-70, 42], [70, 45]],
    [[-30, -2], [-26, 70]], [[-14, -4], [-16, 70]], [[8, -4], [12, 70]], [[26, -2], [22, 70]], [[-40, 0], [30, 50]],
  ];
  return { sea, river: [...left, ...right.reverse()], parks, roads };
}

export const BASEMAP = buildBasemap();
