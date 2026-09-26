// @vitest-environment node
import { describe, it, expect } from "vitest";
import { cameraAt, makeProjector, mixHex, segment, withAlpha, type Camera, type CameraKeyframe } from "./heroMapMath";

const LEVEL: Camera = { x: 0, y: 0, z: 0, yaw: 0, pitch: 0, focal: 1 };

describe("makeProjector", () => {
  it("projects a point straight ahead onto the vanishing point", () => {
    const project = makeProjector(800, 400, LEVEL);
    const q = project(0, 0, 10)!;
    expect(q[0]).toBeCloseTo(400);
    expect(q[1]).toBeCloseTo(200);
    expect(q[2]).toBeCloseTo(10);
  });

  it("drops points behind the camera instead of mirroring them", () => {
    expect(makeProjector(800, 400, LEVEL)(0, 0, -5)).toBeNull();
  });

  it("puts east on the right and up at the top of the screen", () => {
    const project = makeProjector(800, 400, LEVEL);
    expect(project(1, 0, 10)![0]).toBeGreaterThan(400);
    expect(project(0, 1, 10)![1]).toBeLessThan(200);
  });

  it("honours an off-center vanishing point", () => {
    expect(makeProjector(800, 400, LEVEL, 480)(0, 0, 10)![0]).toBeCloseTo(480);
  });

  it("looking straight down, maps north to the top of the screen", () => {
    const project = makeProjector(800, 400, { ...LEVEL, y: 20, pitch: Math.PI / 2 });
    const north = project(0, 0, 5)!;
    const south = project(0, 0, -5)!;
    expect(north[1]).toBeLessThan(south[1]);
  });

  it("turning toward +x brings an eastern point to the center", () => {
    const project = makeProjector(800, 400, { ...LEVEL, yaw: Math.PI / 2 });
    expect(project(10, 0, 0)![0]).toBeCloseTo(400);
  });
});

describe("cameraAt", () => {
  const keys: CameraKeyframe[] = [
    { t: 0, x: 0, y: 10, z: 0, yaw: 0, pitch: 0.5, focal: 1 },
    { t: 2, x: 10, y: 20, z: 4, yaw: 1, pitch: 1, focal: 1.2 },
  ];

  it("hits keyframes exactly and holds outside the keyed range", () => {
    expect(cameraAt(0, keys).x).toBe(0);
    expect(cameraAt(2, keys).x).toBe(10);
    expect(cameraAt(-1, keys).x).toBe(0);
    expect(cameraAt(99, keys).y).toBe(20);
  });

  it("passes the midpoint halfway through a symmetric ease", () => {
    expect(cameraAt(1, keys).x).toBeCloseTo(5);
  });
});

describe("colors", () => {
  it("mixHex returns the endpoints and clamps outside 0..1", () => {
    expect(mixHex("#000000", "#ffffff", 0)).toBe("#000000");
    expect(mixHex("#000000", "#ffffff", 1)).toBe("#ffffff");
    expect(mixHex("#000000", "#ffffff", 5)).toBe("#ffffff");
    expect(mixHex("#000000", "#ffffff", 0.5)).toBe("#808080");
  });

  it("withAlpha emits rgba with a clamped alpha", () => {
    expect(withAlpha("#43c5ba", 0.5)).toBe("rgba(67,197,186,0.5)");
    expect(withAlpha("#43c5ba", 2)).toBe("rgba(67,197,186,1)");
  });
});

describe("segment", () => {
  it("clamps progress through a window", () => {
    expect(segment(0, 1, 3)).toBe(0);
    expect(segment(2, 1, 3)).toBe(0.5);
    expect(segment(9, 1, 3)).toBe(1);
  });
});
