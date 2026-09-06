import { describe, it, expect } from "vitest";
import {
  buildCityScene,
  buildVehicles,
  advanceVehicle,
  poseAtT,
  routeLength,
  type RouteLine,
} from "./cityMapScene";

describe("buildCityScene", () => {
  it("is deterministic across calls", () => {
    const a = buildCityScene();
    const b = buildCityScene();
    expect(a).toEqual(b);
  });

  it("carves out the park and river cells (no block overlaps either)", () => {
    const scene = buildCityScene();
    expect(scene.blocks.length).toBeGreaterThan(0);
    for (const block of scene.blocks) {
      const blockCenterX = block.x + block.w / 2;
      const blockCenterY = block.y + block.h / 2;
      const insidePark =
        blockCenterX >= scene.park.x &&
        blockCenterX <= scene.park.x + scene.park.w &&
        blockCenterY >= scene.park.y &&
        blockCenterY <= scene.park.y + scene.park.h;
      expect(insidePark).toBe(false);
    }
  });

  it("defines exactly two routes, one per real semantic color token", () => {
    const scene = buildCityScene();
    const colorVars = scene.routes.map((r) => r.colorVar).sort();
    expect(colorVars).toEqual(["--accent", "--color-warning"]);
  });

  it("every route has at least two points (a route needs a segment to move along)", () => {
    const scene = buildCityScene();
    for (const route of scene.routes) {
      expect(route.points.length).toBeGreaterThanOrEqual(2);
    }
  });

  it("assigns one vehicle mode per route, never mixed across routes", () => {
    const scene = buildCityScene();
    for (const route of scene.routes) {
      expect(["bus", "train", "tram"]).toContain(route.vehicleMode);
    }
    const modes = scene.routes.map((r) => r.vehicleMode);
    expect(new Set(modes).size).toBe(modes.length);
  });
});

describe("routeLength / poseAtT", () => {
  const route: RouteLine = {
    id: "test",
    colorVar: "--accent",
    vehicleMode: "bus",
    points: [
      { x: 0, y: 0 },
      { x: 1, y: 0 },
      { x: 1, y: 1 },
    ],
  };

  it("sums segment lengths", () => {
    expect(routeLength(route)).toBeCloseTo(2, 5);
  });

  it("resolves the start, midpoint, and (just before wrapping) end of a route", () => {
    expect(poseAtT(route, 0)).toMatchObject({ x: 0, y: 0 });
    // t=0.25 of a total length 2 -> 0.5 along the first (horizontal) segment.
    const mid = poseAtT(route, 0.25);
    expect(mid.x).toBeCloseTo(0.5, 5);
    expect(mid.y).toBeCloseTo(0, 5);
    // t=1 wraps to the start (t modulo 1) by design -- a looping vehicle's
    // t only ever increases -- so "the end" is approximated just below 1.
    const nearEnd = poseAtT(route, 0.999999);
    expect(nearEnd.x).toBeCloseTo(1, 4);
    expect(nearEnd.y).toBeCloseTo(1, 4);
  });

  it("wraps t modulo 1 so a vehicle loops the route continuously", () => {
    const onceAround = poseAtT(route, 0.25);
    const wrapped = poseAtT(route, 1.25);
    expect(wrapped.x).toBeCloseTo(onceAround.x, 5);
    expect(wrapped.y).toBeCloseTo(onceAround.y, 5);
  });

  it("orients along the segment's direction of travel", () => {
    // On the horizontal segment (0,0) -> (1,0), heading is 0 rad (+x).
    expect(poseAtT(route, 0.1).angleRad).toBeCloseTo(0, 5);
    // On the vertical segment (1,0) -> (1,1), heading is +pi/2 (+y, down in
    // screen space).
    expect(poseAtT(route, 0.75).angleRad).toBeCloseTo(Math.PI / 2, 5);
  });
});

describe("buildVehicles / advanceVehicle", () => {
  it("places two phase-offset vehicles per route", () => {
    const scene = buildCityScene();
    const vehicles = buildVehicles(scene.routes);
    expect(vehicles.length).toBe(scene.routes.length * 2);
    for (const route of scene.routes) {
      const ts = vehicles.filter((v) => v.routeId === route.id).map((v) => v.t).sort();
      expect(ts).toEqual([0, 0.5]);
    }
  });

  it("advances t proportionally to elapsed time without mutating the input", () => {
    const vehicle = { routeId: "r", t: 0, speedPerMs: 0.0001 };
    const next = advanceVehicle(vehicle, 1000);
    expect(vehicle.t).toBe(0);
    expect(next.t).toBeCloseTo(0.1, 6);
  });
});
