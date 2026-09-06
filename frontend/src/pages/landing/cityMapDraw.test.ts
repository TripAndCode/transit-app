import { describe, it, expect, vi } from "vitest";
import { drawScene } from "./cityMapDraw";
import { buildCityScene, buildVehicles, poseAtT } from "./cityMapScene";

/** Minimal recording stand-in for CanvasRenderingContext2D -- jsdom itself
 *  returns null from canvas.getContext("2d") (no `canvas` npm package
 *  installed), so drawScene's own logic is exercised here against a plain
 *  object implementing just the methods/properties it actually calls,
 *  rather than a real rendering context. */
function makeFakeCtx() {
  const calls: string[] = [];
  const ctx = {
    fillStyle: "",
    strokeStyle: "",
    lineWidth: 0,
    lineJoin: "miter" as CanvasLineJoin,
    lineCap: "butt" as CanvasLineCap,
    globalAlpha: 1,
    clearRect: vi.fn(() => calls.push("clearRect")),
    fillRect: vi.fn(() => calls.push("fillRect")),
    beginPath: vi.fn(() => calls.push("beginPath")),
    moveTo: vi.fn(() => calls.push("moveTo")),
    lineTo: vi.fn(() => calls.push("lineTo")),
    stroke: vi.fn(() => calls.push("stroke")),
    fill: vi.fn(() => calls.push("fill")),
    arc: vi.fn(() => calls.push("arc")),
    ellipse: vi.fn(() => calls.push("ellipse")),
    bezierCurveTo: vi.fn(() => calls.push("bezierCurveTo")),
    closePath: vi.fn(() => calls.push("closePath")),
    save: vi.fn(() => calls.push("save")),
    restore: vi.fn(() => calls.push("restore")),
    translate: vi.fn(() => calls.push("translate")),
    rotate: vi.fn(() => calls.push("rotate")),
    scale: vi.fn(() => calls.push("scale")),
  };
  return { ctx: ctx as unknown as CanvasRenderingContext2D, calls };
}

describe("drawScene", () => {
  it("draws without throwing and exercises every drawing primitive it uses", () => {
    const scene = buildCityScene();
    const vehicles = buildVehicles(scene.routes);
    const routesById = new Map(scene.routes.map((r) => [r.id, r]));
    const vehicleDraws = vehicles.map((v) => {
      const route = routesById.get(v.routeId)!;
      return { pose: poseAtT(route, v.t), colorVar: route.colorVar, mode: route.vehicleMode };
    });
    const { ctx, calls } = makeFakeCtx();

    expect(() =>
      drawScene(ctx, 1000, 600, scene, vehicleDraws, { onTime: "#1A8A72", delayed: "#C99A2E" }),
    ).not.toThrow();

    expect(calls).toContain("clearRect");
    expect(calls).toContain("fillRect"); // blocks + park
    expect(calls).toContain("stroke"); // river + routes
    expect(calls.filter((c) => c === "arc").length).toBeGreaterThan(0); // stations + vehicle glow/badges
    expect(calls).toContain("bezierCurveTo"); // Maki glyph curves
    expect(calls).toContain("closePath"); // Maki glyph subpath closes
    // Every vehicle is drawn inside a save/restore pair, isolating its
    // translate/scale from the next vehicle's.
    expect(calls.filter((c) => c === "save").length).toBe(vehicleDraws.length);
    expect(calls.filter((c) => c === "restore").length).toBe(vehicleDraws.length);
  });

  it("is a no-op for a zero-size canvas instead of drawing degenerate geometry", () => {
    const scene = buildCityScene();
    const { ctx, calls } = makeFakeCtx();
    drawScene(ctx, 0, 0, scene, [], { onTime: "#1A8A72", delayed: "#C99A2E" });
    expect(calls).toEqual([]);
  });
});
