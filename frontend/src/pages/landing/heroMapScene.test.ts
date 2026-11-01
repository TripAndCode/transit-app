// @vitest-environment node
import { describe, it, expect } from "vitest";
import {
  HOURLY_24,
  HOURLY_MEANS,
  OVERVIEW,
  PEAK_HOUR,
  QUEUE_ROWS,
  ROUTES,
  SELECTED_ROUTE,
  SELECTED_ROUTE_STOP_AVG,
  SELECTED_TRIP,
  TRIPS,
  TRIP_HISTORY,
  coastZ,
  polyline,
} from "./heroMapScene";

describe("polyline", () => {
  it("interpolates by arc length and clamps outside 0..1", () => {
    const line = polyline([
      [0, 0],
      [3, 0],
      [3, 4],
    ]);
    expect(line.at(0)).toEqual([0, 0]);
    expect(line.at(1)).toEqual([3, 4]);
    expect(line.at(3 / 7)[0]).toBeCloseTo(3);
    expect(line.at(-1)).toEqual([0, 0]);
    expect(line.at(2)).toEqual([3, 4]);
  });
});

describe("sample scene", () => {
  it("runs no tram, since no feed the app reads carries one", () => {
    expect(new Set(ROUTES.map((r) => r.mode))).toEqual(new Set(["bus", "train"]));
  });

  it("keeps the rail lines as scenery: every trip, queue row and route to check is a bus", () => {
    const modeOf = (key: string) => ROUTES.find((r) => r.key === key)?.mode;
    expect(TRIPS.every((tp) => ROUTES[tp.route].mode === "bus")).toBe(true);
    expect(QUEUE_ROWS.every((row) => modeOf(row.route) === "bus")).toBe(true);
    expect(OVERVIEW.routesToCheck.every((row) => modeOf(row.route) === "bus")).toBe(true);
  });

  it("keeps every trip on land and moves each one forward on refresh", () => {
    for (const trip of TRIPS) {
      const [x, z] = ROUTES[trip.route].path.at(trip.u);
      expect(z).toBeGreaterThan(coastZ(x));
      expect(trip.nextU).toBeGreaterThan(trip.u);
      expect(trip.nextU).toBeLessThanOrEqual(1);
    }
  });

  it("tells one story about the selected trip across the queue, the trail and the panel", () => {
    expect(SELECTED_TRIP.route).toBe(SELECTED_ROUTE);
    expect(QUEUE_ROWS[0]).toMatchObject({ route: "bus12", delay: SELECTED_TRIP.delay });
    const latest = TRIP_HISTORY[TRIP_HISTORY.length - 1];
    expect(latest.u).toBeCloseTo(SELECTED_TRIP.u);
    expect(latest.delay).toBe(SELECTED_TRIP.delay);
  });

  it("reports the trip's stops oldest first, each at a stop of the route", () => {
    const stopCount = SELECTED_ROUTE_STOP_AVG.length;
    TRIP_HISTORY.forEach((h, i) => {
      if (i) expect(h.u).toBeGreaterThan(TRIP_HISTORY[i - 1].u);
      expect(h.u * (stopCount - 1)).toBeCloseTo(Math.round(h.u * (stopCount - 1)));
    });
  });

  it("derives the hourly bars from the playback rail's own hours", () => {
    expect(HOURLY_24).toHaveLength(24);
    expect(HOURLY_24.slice(24 - HOURLY_MEANS.length)).toEqual(HOURLY_MEANS);
    expect(HOURLY_24[PEAK_HOUR]).toBe(Math.max(...HOURLY_24));
  });
});
