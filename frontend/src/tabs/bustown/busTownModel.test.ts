import { describe, expect, it } from "vitest";
import type { ReplayResponse } from "../../api/types";
import {
  boundsFromPoints,
  buildBusTownModel,
  desiredWindowKeys,
  fitProjection,
  formatServiceClock,
  mergeReplayWindows,
  onTimeCount,
  positionsAt,
  scrubberStartHour,
  shouldPrefetchNext,
  slowestRoute,
  stopPairsFromWindows,
  stopPointsFromWindows,
  windowStartForHour,
} from "./busTownModel";

function replayWindow(overrides: Partial<ReplayResponse>): ReplayResponse {
  return {
    date: "2026-01-05",
    from_hour: 4,
    to_hour: 7,
    available_dates: ["2026-01-05"],
    stops: { id: [], lat: [], lon: [] },
    routes: [],
    trips: [],
    ...overrides,
  };
}

describe("mergeReplayWindows: stop_idx resolution across windows", () => {
  it("resolves each window's stop_idx against that window's own stops, even when index order differs", () => {
    // Window A: index 0 = stopX, index 1 = stopY.
    const windowA = replayWindow({
      stops: { id: ["stopX", "stopY"], lat: [35.0, 35.1], lon: [139.0, 139.1] },
      trips: [{ trip_id: "t1", route_code: "R1", stop_idx: [0, 1], t: [100, 200], delay: [0, 0] }],
    });
    // Window B: the same two stops, but index order reversed — a naive
    // "merge by stop_idx" (ignoring each window's own stops) would swap them.
    const windowB = replayWindow({
      stops: { id: ["stopY", "stopX"], lat: [35.1, 35.0], lon: [139.1, 139.0] },
      trips: [{ trip_id: "t1", route_code: "R1", stop_idx: [0, 1], t: [300, 400], delay: [0, 0] }],
    });
    const merged = mergeReplayWindows([windowA, windowB]);
    const trip = merged.get("t1")!;
    expect(trip.events.map((e) => e.stopId)).toEqual(["stopX", "stopY", "stopY", "stopX"]);
    expect(trip.events.map((e) => [e.lat, e.lon])).toEqual([
      [35.0, 139.0],
      [35.1, 139.1],
      [35.1, 139.1],
      [35.0, 139.0],
    ]);
  });

  it("drops stop events with null coordinates rather than resolving them to anything", () => {
    const w = replayWindow({
      stops: { id: ["a", "b", "c"], lat: [35.0, null, 35.2], lon: [139.0, null, 139.2] },
      trips: [{ trip_id: "t1", route_code: "R1", stop_idx: [0, 1, 2], t: [100, 200, 300], delay: [0, 0, 0] }],
    });
    const merged = mergeReplayWindows([w]);
    expect(merged.get("t1")!.events.map((e) => e.stopId)).toEqual(["a", "c"]);
  });

  it("carries the last non-null route_code forward at the trip level", () => {
    const windowA = replayWindow({
      stops: { id: ["a", "b"], lat: [35.0, 35.1], lon: [139.0, 139.1] },
      trips: [{ trip_id: "t1", route_code: "R1", stop_idx: [0, 1], t: [100, 200], delay: [0, 0] }],
    });
    const windowB = replayWindow({
      stops: { id: ["a", "b"], lat: [35.0, 35.1], lon: [139.0, 139.1] },
      trips: [{ trip_id: "t1", route_code: null, stop_idx: [0, 1], t: [300, 400], delay: [0, 0] }],
    });
    expect(mergeReplayWindows([windowA, windowB]).get("t1")!.routeCode).toBe("R1");
  });
});

describe("monotone envelope: at most one position per trip per second", () => {
  it("clamps a non-monotonic t sequence to be non-decreasing", () => {
    const w = replayWindow({
      stops: { id: ["a", "b", "c", "d"], lat: [35.0, 35.1, 35.2, 35.3], lon: [139.0, 139.1, 139.2, 139.3] },
      trips: [{ trip_id: "t1", route_code: "R1", stop_idx: [0, 1, 2, 3], t: [100, 300, 200, 400], delay: [0, 0, 0, 0] }],
    });
    const merged = mergeReplayWindows([w]);
    const ts = merged.get("t1")!.events.map((e) => e.t);
    expect(ts).toEqual([100, 300, 300, 400]);
    for (let i = 1; i < ts.length; i++) expect(ts[i]).toBeGreaterThanOrEqual(ts[i - 1]);
  });

  it("negative control: without clamping, positionsAt would report two positions for one trip at once", () => {
    // This directly tests the invariant the clamp exists to guarantee: at any
    // single second, a trip yields at most one drawable position. Skipping
    // the clamp (naively sorting by t and connecting events as-is) produces
    // two overlapping segments covering the same second — this is exactly
    // the failure mode `monotoneEnvelope` exists to rule out.
    const events = [
      { t: 100, delaySec: 0, lat: 35.0, lon: 139.0, stopId: "a" },
      { t: 300, delaySec: 0, lat: 35.1, lon: 139.1, stopId: "b" },
      { t: 200, delaySec: 0, lat: 35.2, lon: 139.2, stopId: "c" },
      { t: 400, delaySec: 0, lat: 35.3, lon: 139.3, stopId: "d" },
    ];
    const unclamped = new Map([["t1", { tripId: "t1", routeCode: "R1", events }]]);
    const model = buildBusTownModel(unclamped);
    // sec=250 falls inside both the unclamped b->c segment (300->200, i.e.
    // t in [200,300]) and the a->b segment is not active, but c->d (200->400)
    // also covers 250 — two segments of the same trip both claim this second.
    const covering = model.get("t1")!.filter((s) => 250 >= s.t0 && 250 <= s.t1);
    expect(covering.length).toBeGreaterThan(1);

    // The real pipeline runs events through mergeReplayWindows first, which
    // clamps them, so the same input never reaches buildBusTownModel unclamped.
    const w = replayWindow({
      stops: { id: ["a", "b", "c", "d"], lat: [35.0, 35.1, 35.2, 35.3], lon: [139.0, 139.1, 139.2, 139.3] },
      trips: [{ trip_id: "t1", route_code: "R1", stop_idx: [0, 1, 2, 3], t: [100, 300, 200, 400], delay: [0, 0, 0, 0] }],
    });
    const clampedModel = buildBusTownModel(mergeReplayWindows([w]));
    const clampedCovering = clampedModel.get("t1")!.filter((s) => 250 >= s.t0 && 250 <= s.t1);
    expect(clampedCovering.length).toBe(1);
  });
});

describe("positionsAt: interpolation", () => {
  const w = replayWindow({
    stops: { id: ["a", "b"], lat: [35.0, 35.2], lon: [139.0, 139.4] },
    trips: [{ trip_id: "t1", route_code: "R1", stop_idx: [0, 1], t: [100, 200], delay: [10, 20] }],
  });
  const model = buildBusTownModel(mergeReplayWindows([w]));

  it("sits exactly at the start event", () => {
    const [bus] = positionsAt(model, 100);
    expect(bus.lat).toBeCloseTo(35.0);
    expect(bus.lon).toBeCloseTo(139.0);
  });

  it("sits exactly at the end event", () => {
    const [bus] = positionsAt(model, 200);
    expect(bus.lat).toBeCloseTo(35.2);
    expect(bus.lon).toBeCloseTo(139.4);
  });

  it("interpolates linearly between events", () => {
    const [bus] = positionsAt(model, 150);
    expect(bus.lat).toBeCloseTo(35.1);
    expect(bus.lon).toBeCloseTo(139.2);
  });

  it("uses the segment's start-event delay, not an interpolated one", () => {
    const [bus] = positionsAt(model, 150);
    expect(bus.delayMin).toBeCloseTo(10 / 60);
  });

  it("is not drawn before the first or after the last observed event", () => {
    expect(positionsAt(model, 99)).toEqual([]);
    expect(positionsAt(model, 201)).toEqual([]);
  });
});

describe("onTimeCount", () => {
  it("counts a bus as on time only under the ok delay band (< 90s)", () => {
    const buses = [
      { tripId: "a", routeCode: "R1", lat: 0, lon: 0, delayMin: 0 },
      { tripId: "b", routeCode: "R1", lat: 0, lon: 0, delayMin: 89 / 60 },
      { tripId: "c", routeCode: "R1", lat: 0, lon: 0, delayMin: 3 },
    ];
    expect(onTimeCount(buses)).toEqual({ onTime: 2, total: 3 });
  });
});

describe("slowestRoute", () => {
  it("picks the route with the highest mean delay, skipping null route_code trips", () => {
    const trips = new Map([
      ["t1", { tripId: "t1", routeCode: "R1", events: [{ t: 0, delaySec: 60, lat: 0, lon: 0, stopId: "a" }] }],
      ["t2", { tripId: "t2", routeCode: "R2", events: [{ t: 0, delaySec: 600, lat: 0, lon: 0, stopId: "a" }] }],
      ["t3", { tripId: "t3", routeCode: null, events: [{ t: 0, delaySec: 9999, lat: 0, lon: 0, stopId: "a" }] }],
    ]);
    const result = slowestRoute(trips, { R1: "Red Line", R2: "Green Line" });
    expect(result).toEqual({ routeCode: "R2", name: "Green Line", meanDelayMin: 10 });
  });

  it("breaks ties on route_code ascending", () => {
    const trips = new Map([
      ["t1", { tripId: "t1", routeCode: "R2", events: [{ t: 0, delaySec: 60, lat: 0, lon: 0, stopId: "a" }] }],
      ["t2", { tripId: "t2", routeCode: "R1", events: [{ t: 0, delaySec: 60, lat: 0, lon: 0, stopId: "a" }] }],
    ]);
    expect(slowestRoute(trips, {})?.routeCode).toBe("R1");
  });

  it("returns null when no trip has a route_code", () => {
    const trips = new Map([["t1", { tripId: "t1", routeCode: null, events: [] }]]);
    expect(slowestRoute(trips, {})).toBeNull();
  });
});

describe("stop points and pairs", () => {
  it("dedupes stop points across windows and skips null coordinates", () => {
    const w1 = replayWindow({ stops: { id: ["a", "b"], lat: [35.0, null], lon: [139.0, null] } });
    const w2 = replayWindow({ stops: { id: ["a", "c"], lat: [35.0, 35.2], lon: [139.0, 139.2] } });
    expect(stopPointsFromWindows([w1, w2])).toEqual([
      { id: "a", lat: 35.0, lon: 139.0 },
      { id: "c", lat: 35.2, lon: 139.2 },
    ]);
  });

  it("dedupes consecutive stop pairs regardless of direction", () => {
    const w = replayWindow({
      stops: { id: ["a", "b"], lat: [35.0, 35.1], lon: [139.0, 139.1] },
      trips: [
        { trip_id: "t1", route_code: "R1", stop_idx: [0, 1], t: [0, 100], delay: [0, 0] },
        { trip_id: "t2", route_code: "R1", stop_idx: [1, 0], t: [0, 100], delay: [0, 0] },
      ],
    });
    expect(stopPairsFromWindows([w])).toHaveLength(1);
  });
});

describe("fitProjection", () => {
  it("maps the bounding box corners inside the padded area, north up", () => {
    const bounds: [number, number, number, number] = [139.0, 35.0, 139.1, 35.1];
    const project = fitProjection(bounds, 200, 200, 10);
    const north = project(35.1, 139.05);
    const south = project(35.0, 139.05);
    expect(north.y).toBeLessThan(south.y);
    const nw = project(35.1, 139.0);
    expect(nw.x).toBeGreaterThanOrEqual(10);
    expect(nw.y).toBeGreaterThanOrEqual(10);
  });
});

describe("boundsFromPoints", () => {
  it("returns null for an empty list", () => {
    expect(boundsFromPoints([])).toBeNull();
  });

  it("covers every point", () => {
    expect(boundsFromPoints([{ lat: 35.0, lon: 139.0 }, { lat: 35.2, lon: 139.4 }])).toEqual([
      139.0, 35.0, 139.4, 35.2,
    ]);
  });
});

describe("window grid anchoring", () => {
  it("anchors the 3-hour grid on the day's own default from_hour, not a fixed clock grid", () => {
    expect(windowStartForHour(5, 5)).toBe(5);
    expect(windowStartForHour(7.9, 5)).toBe(5);
    expect(windowStartForHour(8, 5)).toBe(8);
    expect(windowStartForHour(4, 5)).toBe(2);
  });

  it("clamps the scrubber start to the earlier of the usual cutover and the day's own default window", () => {
    expect(scrubberStartHour(5)).toBe(4);
    expect(scrubberStartHour(2)).toBe(2);
    expect(scrubberStartHour(null)).toBe(4);
  });

  it("prefetches the next window only once playback is close enough to the current window's end", () => {
    expect(shouldPrefetchNext(28000, 28800, 1)).toBe(false);
    expect(shouldPrefetchNext(28799, 28800, 1, 4)).toBe(true);
    expect(shouldPrefetchNext(28700, 28800, 60, 4)).toBe(true);
    expect(shouldPrefetchNext(28801, 28800, 1)).toBe(false);
  });

  it("desired window keys include the previous window and, near the end, the next one", () => {
    const originHour = 5;
    const midWindow = desiredWindowKeys("2026-01-05", originHour, 6 * 3600, 1);
    expect(midWindow.map((k) => k.fromHour)).toEqual([2, 5]);

    const nearEnd = desiredWindowKeys("2026-01-05", originHour, 8 * 3600 - 2, 1);
    expect(nearEnd.map((k) => k.fromHour)).toEqual([2, 5, 8]);
  });
});

describe("formatServiceClock", () => {
  it("does not wrap past 24 hours", () => {
    expect(formatServiceClock(25 * 3600 + 14 * 60)).toBe("25:14");
  });

  it("pads single-digit hours and minutes", () => {
    expect(formatServiceClock(5 * 60)).toBe("00:05");
  });
});
