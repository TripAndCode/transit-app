// @vitest-environment node
import { describe, it, expect } from "vitest";
import { backOut, expoInOut, gaussianBump, luminance, makeProjector, mixCamera, mixHex, segment, withAlpha, type Camera } from "./heroMapMath";

const LEVEL: Camera = { x: 0, y: 0, z: 0, yaw: 0, pitch: 0, focal: 1 };

describe("makeProjector", () => {
  it("projects a point straight ahead onto the vanishing point", () => {
    const q = makeProjector(800, 400, LEVEL)(0, 0, 10)!;
    expect(q[0]).toBeCloseTo(400);
    expect(q[1]).toBeCloseTo(200);
    expect(q[2]).toBeCloseTo(10);
    expect(q[3]).toBeCloseTo(40);
  });

  it("drops points behind the camera instead of mirroring them", () => {
    expect(makeProjector(800, 400, LEVEL)(0, 0, -5)).toBeNull();
  });

  it("honours an off-center vanishing point on both axes", () => {
    const q = makeProjector(800, 400, LEVEL, 480, 300)(0, 0, 10)!;
    expect(q[0]).toBeCloseTo(480);
    expect(q[1]).toBeCloseTo(300);
  });

  it("looking straight down, maps north to the top of the screen and east to the right", () => {
    const project = makeProjector(800, 400, { ...LEVEL, y: 20, pitch: Math.PI / 2 });
    expect(project(0, 0, 5)![1]).toBeLessThan(project(0, 0, -5)![1]);
    expect(project(5, 0, 0)![0]).toBeGreaterThan(project(-5, 0, 0)![0]);
  });
});

describe("mixCamera", () => {
  it("returns each endpoint and interpolates between them", () => {
    const a: Camera = { x: 0, y: 10, z: 0, yaw: 0, pitch: 0.5, focal: 1 };
    const b: Camera = { x: 10, y: 20, z: 4, yaw: 1, pitch: 1, focal: 2 };
    expect(mixCamera(a, b, 0)).toEqual(a);
    expect(mixCamera(a, b, 1)).toEqual(b);
    expect(mixCamera(a, b, 0.5).x).toBe(5);
  });
});

describe("easing", () => {
  it("expoInOut is pinned at both ends and symmetric about the middle", () => {
    expect(expoInOut(0)).toBe(0);
    expect(expoInOut(1)).toBe(1);
    expect(expoInOut(0.5)).toBeCloseTo(0.5);
  });

  it("backOut overshoots before settling at 1", () => {
    expect(backOut(1)).toBeCloseTo(1);
    expect(Math.max(...Array.from({ length: 20 }, (_, i) => backOut(i / 20)))).toBeGreaterThan(1);
  });

  it("gaussianBump peaks at its center and falls off symmetrically", () => {
    expect(gaussianBump(0.5, 0.5, 0.2)).toBe(1);
    expect(gaussianBump(0.3, 0.5, 0.2)).toBeCloseTo(gaussianBump(0.7, 0.5, 0.2));
    expect(gaussianBump(0.7, 0.5, 0.2)).toBeCloseTo(Math.exp(-1));
  });

  it("segment clamps progress through a window", () => {
    expect(segment(0, 1, 3)).toBe(0);
    expect(segment(2, 1, 3)).toBe(0.5);
    expect(segment(9, 1, 3)).toBe(1);
  });
});

describe("colors", () => {
  it("mixHex returns the endpoints and clamps outside 0..1", () => {
    expect(mixHex("#000000", "#ffffff", 0)).toBe("#000000");
    expect(mixHex("#000000", "#ffffff", 5)).toBe("#ffffff");
    expect(mixHex("#000000", "#ffffff", 0.5)).toBe("#808080");
  });

  it("withAlpha emits rgba with a clamped alpha", () => {
    expect(withAlpha("#43c5ba", 0.5)).toBe("rgba(67,197,186,0.5)");
    expect(withAlpha("#43c5ba", 2)).toBe("rgba(67,197,186,1)");
  });

  it("luminance separates the app's light and dark page colors", () => {
    expect(luminance("#fafaf8")).toBeGreaterThan(0.9);
    expect(luminance("#0F1119")).toBeLessThan(0.02);
  });
});
