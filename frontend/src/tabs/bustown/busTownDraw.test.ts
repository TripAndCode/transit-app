import { describe, it, expect, vi, afterEach } from "vitest";
import { drawBusTownFrame, type BusTownPalette } from "./busTownDraw";
import type { BusTownBus, Projection } from "./busTownModel";

const IDENTITY_PROJECTION: Projection = (lat, lon) => ({ x: lon, y: lat });

/** Distinct from `resolveBusTownPalette`'s own fallback (`#A8391F`), so a
 *  regression that falls back to that constant instead of using this value
 *  still fails the fill-colour assertion below. */
const PALETTE: BusTownPalette = {
  background: "#ffffff",
  line: "#D6DDE6",
  stopDot: "#5E6982",
  stopRing: "#F6F7FB",
  severe: "#123456",
};

const SEVERE_BUS: BusTownBus = { tripId: "t1", routeCode: null, lat: 35.0, lon: 139.0, delayMin: 10 };

function makeFakeCtx() {
  const fillStylesAtArc: string[] = [];
  const ctx = {
    clearRect: vi.fn(),
    fillRect: vi.fn(),
    beginPath: vi.fn(),
    moveTo: vi.fn(),
    lineTo: vi.fn(),
    stroke: vi.fn(),
    arc: vi.fn(() => fillStylesAtArc.push(ctx.fillStyle)),
    fill: vi.fn(),
    fillStyle: "",
    strokeStyle: "",
    lineWidth: 0,
    globalAlpha: 1,
  } as unknown as CanvasRenderingContext2D & { fillStyle: string };
  return { ctx, fillStylesAtArc };
}

describe("drawBusTownFrame severe-delay colour", () => {
  afterEach(() => vi.restoreAllMocks());

  it("uses the palette's resolved severe colour without calling getComputedStyle", () => {
    const getComputedStyleSpy = vi.spyOn(window, "getComputedStyle");
    const { ctx, fillStylesAtArc } = makeFakeCtx();

    drawBusTownFrame(ctx, 100, 100, IDENTITY_PROJECTION, [], [], [SEVERE_BUS], PALETTE);

    expect(getComputedStyleSpy).not.toHaveBeenCalled();
    expect(fillStylesAtArc).toEqual([PALETTE.severe]);
  });
});
