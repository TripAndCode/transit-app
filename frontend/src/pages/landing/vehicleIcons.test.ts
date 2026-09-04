import { describe, it, expect, vi } from "vitest";
import { drawMakiPath, MAKI_VIEWBOX_SIZE, VEHICLE_ICON_PATHS, type VehicleMode } from "./vehicleIcons";

// Minimal recording stand-in for CanvasRenderingContext2D, matching the
// pattern in cityMapDraw.test.ts -- jsdom has no real canvas/Path2D support,
// so drawMakiPath's own command-replay logic is exercised against a plain
// object implementing just the methods it calls.
function makeFakeCtx() {
  const calls: string[] = [];
  const ctx = {
    moveTo: vi.fn(() => calls.push("moveTo")),
    lineTo: vi.fn(() => calls.push("lineTo")),
    bezierCurveTo: vi.fn(() => calls.push("bezierCurveTo")),
    closePath: vi.fn(() => calls.push("closePath")),
  };
  return { ctx: ctx as unknown as CanvasRenderingContext2D, calls };
}

describe("VEHICLE_ICON_PATHS", () => {
  it("defines exactly bus, train, and tram as non-empty path strings", () => {
    const modes = Object.keys(VEHICLE_ICON_PATHS).sort();
    expect(modes).toEqual(["bus", "train", "tram"]);
    for (const mode of modes as VehicleMode[]) {
      expect(VEHICLE_ICON_PATHS[mode].length).toBeGreaterThan(0);
    }
  });

  it("uses the native 15x15 Maki viewBox", () => {
    expect(MAKI_VIEWBOX_SIZE).toBe(15);
  });
});

describe("drawMakiPath", () => {
  it.each(Object.keys(VEHICLE_ICON_PATHS) as VehicleMode[])(
    "replays the %s glyph's path commands without throwing",
    (mode) => {
      const { ctx, calls } = makeFakeCtx();
      expect(() => drawMakiPath(ctx, VEHICLE_ICON_PATHS[mode])).not.toThrow();
      expect(calls).toContain("moveTo");
      expect(calls).toContain("bezierCurveTo");
      expect(calls).toContain("closePath");
    },
  );

  it("draws a horizontal/vertical lineto as an ordinary lineTo call", () => {
    const { ctx, calls } = makeFakeCtx();
    drawMakiPath(ctx, "M0 0H5V5Z");
    expect(calls).toEqual(["moveTo", "lineTo", "lineTo", "closePath"]);
  });

  it("reflects the smooth-curve (S) control point off the previous C's second control point", () => {
    const { ctx } = makeFakeCtx();
    const calledWith: number[][] = [];
    ctx.bezierCurveTo = vi.fn((...args: number[]) => {
      calledWith.push(args);
    }) as unknown as typeof ctx.bezierCurveTo;

    // C ends at (10,0) with second control point (8,0); a following
    // S 12,5 14,0 should reflect the first control point through (10,0):
    // (2*10-8, 2*0-0) = (12, 0).
    drawMakiPath(ctx, "M0,0C2,0,8,0,10,0S12,5,14,0");

    expect(calledWith[1][0]).toBeCloseTo(12, 5);
    expect(calledWith[1][1]).toBeCloseTo(0, 5);
  });

  it("does not reflect when S follows a non-curve command", () => {
    const { ctx } = makeFakeCtx();
    const calledWith: number[][] = [];
    ctx.bezierCurveTo = vi.fn((...args: number[]) => {
      calledWith.push(args);
    }) as unknown as typeof ctx.bezierCurveTo;

    drawMakiPath(ctx, "M0,0L10,0S12,5,14,0");

    // No reflection available -> first control point equals the current
    // point (10,0).
    expect(calledWith[0][0]).toBeCloseTo(10, 5);
    expect(calledWith[0][1]).toBeCloseTo(0, 5);
  });
});
