// @vitest-environment node
import { describe, it, expect } from "vitest";
import { drawHeroFrame, type HeroLabels, type HeroPalette } from "./heroMapDraw";
import { buildHeroMap } from "./heroMapScene";
import { SEQUENCE_END, frameAt } from "./heroMapTimeline";

/** Recording stand-in for CanvasRenderingContext2D (jsdom has none): every
 *  method is a no-op that logs its name, `fillText` also logs its text, and
 *  gradients accept color stops. */
function makeFakeCtx() {
  const calls: string[] = [];
  const texts: string[] = [];
  const gradient = { addColorStop: () => {} };
  const state: Record<string | symbol, unknown> = {};
  const ctx = new Proxy(state, {
    get(target, prop) {
      if (prop in target) return target[prop];
      if (prop === "createLinearGradient" || prop === "createRadialGradient") return () => gradient;
      if (prop === "measureText") return (s: string) => ({ width: s.length * 7 });
      if (prop === "fillText") return (s: string) => texts.push(s);
      return () => calls.push(String(prop));
    },
    set(target, prop, value) {
      target[prop] = value;
      return true;
    },
  });
  return { ctx: ctx as unknown as CanvasRenderingContext2D, calls, texts };
}

const PALETTE: HeroPalette = {
  background: "#0F1119",
  text: "#FAFAFF",
  textMuted: "#C9CBDA",
  accent: "#43c5ba",
  accentStrong: "#6FD8CC",
  warning: "#C99A2E",
  fontBody: "sans-serif",
  fontMono: "monospace",
};

const LABELS: HeroLabels = {
  stations: { central: "Central", harbor: "Harbor", west: "West", eastHill: "East Hill", north: "North", seaside: "Seaside" },
  districts: { north: "N", riverside: "R", central: "C", port: "P", east: "E" },
  captions: {
    0: { title: "cap-live", body: "b0" },
    1: { title: "cap-sections", body: "b1" },
    2: { title: "cap-towers", body: "b2" },
  },
  calloutRoute: "route-12",
  calloutWeekOverWeek: (m) => `wow ${m.toFixed(1)}`,
  towerLabel: "tower-label",
  legendOnTime: "on-time",
  legendDelayed: "late",
  hud: ["hud-title", "hud-scope", "hud-sample"],
  delay: (m) => `+${m.toFixed(1)}`,
};

const map = buildHeroMap();

describe("drawHeroFrame", () => {
  it("renders every point of the script without throwing", () => {
    for (let t = 0; t <= 20; t += 0.25) {
      const { ctx } = makeFakeCtx();
      expect(() => drawHeroFrame(ctx, 1400, 600, frameAt(t), map, PALETTE, LABELS)).not.toThrow();
    }
  });

  it("names the worst section during the callout, with its delay and weekly change", () => {
    const { ctx, texts } = makeFakeCtx();
    drawHeroFrame(ctx, 1400, 600, frameAt(7.5), map, PALETTE, LABELS);
    expect(texts).toContain("route-12");
    expect(texts).toContain(`+${map.hotspot.delay.toFixed(1)}`);
    expect(texts).toContain("wow 2.1");
    expect(texts).toContain("cap-sections");
  });

  it("labels the tallest tower in the finished still, alongside the sample-data notice", () => {
    const { ctx, texts } = makeFakeCtx();
    drawHeroFrame(ctx, 1400, 600, frameAt(SEQUENCE_END), map, PALETTE, LABELS);
    expect(texts).toContain("tower-label");
    expect(texts).toContain(`+${map.tallestTower.delay.toFixed(1)}`);
    expect(texts).toContain("hud-sample");
  });

  it("drops corner captions, legend and HUD when the headline covers a narrow canvas", () => {
    const { ctx, texts } = makeFakeCtx();
    drawHeroFrame(ctx, 600, 600, frameAt(SEQUENCE_END), map, PALETTE, LABELS);
    expect(texts).not.toContain("cap-towers");
    expect(texts).not.toContain("on-time");
    expect(texts).not.toContain("hud-sample");
  });
});
