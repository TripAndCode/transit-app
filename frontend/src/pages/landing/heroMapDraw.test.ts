// @vitest-environment node
import { describe, it, expect } from "vitest";
import type { HeroLabels, HeroPalette } from "./heroCanvas";
import { drawHeroFrame } from "./heroMapDraw";
import { DURATION, LOOP_START, frameAt } from "./heroMapTimeline";
import { WIDE_LAYOUT_MIN_WIDTH, layoutFor } from "./heroPanelDraw";

/** Recording stand-in for CanvasRenderingContext2D (jsdom has none): every
 *  method is a no-op, `fillText` records its text, and gradients/metrics
 *  return plausible stand-ins. */
function makeFakeCtx() {
  const texts: string[] = [];
  const state: Record<string | symbol, unknown> = {};
  const ctx = new Proxy(state, {
    get(target, prop) {
      if (prop in target) return target[prop];
      if (prop === "measureText") return (s: string) => ({ width: s.length * 7 });
      if (prop === "fillText") return (s: string) => texts.push(s);
      return () => {};
    },
    set(target, prop, value) {
      target[prop] = value;
      return true;
    },
  });
  return { ctx: ctx as unknown as CanvasRenderingContext2D, texts };
}

const PALETTE: HeroPalette = {
  land: "#efede7",
  water: "#d3e2e8",
  park: "#dde9d8",
  road: "#ffffff",
  rail: "#7d838a",
  surface: "#ffffff",
  text: "#2a2a2a",
  muted: "#6e6e6e",
  rule: "#e2e2e0",
  accent: "#187b80",
  delay: { ok: "#2EA87A", mild: "#C99A2E", moderate: "#D4622A", severe: "#A8391F" },
  fontBody: "sans-serif",
  fontMono: "monospace",
};

const LABELS: HeroLabels = {
  screens: { live: "tag-live", period: "tag-period" },
  captions: { 0: "cap-live", 1: "cap-trip", 2: "cap-play", 3: "cap-overview" },
  routes: { rapid: "Rapid", local: "Local", tram3: "Tram 3", bus12: "Route 12", bus7: "Route 7", bus3: "Route 3" },
  stops: { konan: "Konan", shiyakusho: "City Hall", central: "Central", honmachi: "Honmachi", higashidai: "Higashidai", minatomachi: "Minato" },
  queueTitle: "queue-title",
  kpi: { observed: "kpi-observed", delayedFivePlus: "kpi-delayed", onTime: "kpi-ontime" },
  legendBands: ["b1", "b2", "b3", "b4"],
  legendDisclosure: "not-gps",
  tripHeading: "trip-heading",
  tripChartTitle: "trip-chart",
  tripNote: "trip-note",
  refresh: "refresh-chip",
  playback: "play-the-day",
  playbackSpeed: "1x",
  hourlyTitle: "hourly-title",
  peak: "peak",
  overviewAverage: "network-avg",
  overviewChange: "change",
  overviewDelayedRoutes: "routes-delayed",
  routesToCheck: "routes-to-check",
  sampleNotice: "sample-notice",
  delayShort: (m) => `+${m}`,
  minutes: (m) => `${m.toFixed(1)}m`,
  hourOfDay: (h) => `${h}h`,
  clock: (h) => `${h}:00`,
  minuteUnit: "m",
};

function textsAt(t: number, width = 1440, height = 740) {
  const { ctx, texts } = makeFakeCtx();
  drawHeroFrame(ctx, width, height, frameAt(t), PALETTE, LABELS);
  return texts;
}

describe("drawHeroFrame", () => {
  it("renders every moment of the script, wide and narrow, without throwing", () => {
    for (let t = 0; t <= DURATION; t += 0.1) {
      expect(() => textsAt(t)).not.toThrow();
      expect(() => textsAt(t, 390, 700)).not.toThrow();
    }
  });

  it("always says the figures are samples", () => {
    for (const t of [0, LOOP_START, 8, 12, 15]) expect(textsAt(t)).toContain("sample-notice");
  });

  it("shows the live queue with the map's legend and disclosure", () => {
    const texts = textsAt(LOOP_START);
    expect(texts).toEqual(expect.arrayContaining(["tag-live", "queue-title", "kpi-observed", "kpi-delayed", "kpi-ontime", "not-gps", "cap-live"]));
    expect(texts).toContain("Route 12　Honmachi");
  });

  it("labels the opened trip's stops on the map, then lands them in the trip chart", () => {
    const mid = textsAt(6.5);
    expect(mid).toEqual(expect.arrayContaining(["Konan +0", "Central +3", "Honmachi +6", "08:12 +6", "trip-heading", "trip-chart"]));
    expect(textsAt(8.6)).toEqual(expect.arrayContaining(["Konan", "City Hall", "Central", "Honmachi", "trip-note"]));
  });

  it("shows the playback rail and clock, then the hourly bars' peak", () => {
    expect(textsAt(11.6)).toEqual(expect.arrayContaining(["play-the-day", "1x", "tag-period", "hourly-title", "9h", "peak"]));
  });

  it("builds the period overview", () => {
    expect(textsAt(15.2)).toEqual(expect.arrayContaining(["network-avg", "change", "routes-delayed", "routes-to-check", "Route 12"]));
  });

  it("drops the panel and its morphs when the headline spans a narrow canvas", () => {
    for (const t of [LOOP_START, 7.3, 10.4, 15.2]) {
      const texts = textsAt(t, 390, 700);
      expect(texts).not.toContain("queue-title");
      expect(texts).not.toContain("trip-chart");
      expect(texts).not.toContain("network-avg");
    }
  });
});

describe("layoutFor", () => {
  it("keeps the panel and the map's focus clear of the headline column", () => {
    for (const width of [WIDE_LAYOUT_MIN_WIDTH, 1280, 1440, 1920, 2560]) {
      const l = layoutFor(width, 740);
      expect(l.panel).not.toBeNull();
      expect(l.panel!.x).toBeGreaterThan(width * 0.48);
      expect(l.panel!.x + l.panel!.w).toBeLessThan(width);
      expect(l.focusX).toBeGreaterThan(width * 0.48);
      expect(l.focusX).toBeLessThan(l.panel!.x);
      expect(l.rail.x + l.rail.w).toBeLessThan(l.panel!.x);
    }
  });

  it("fits the panel inside short heroes", () => {
    const l = layoutFor(1440, 560);
    expect(l.panel!.y).toBeGreaterThanOrEqual(0);
    expect(l.panel!.y + l.panel!.h).toBeLessThanOrEqual(560);
  });

  it("has no panel below the wide breakpoint and pushes the map under the headline", () => {
    const l = layoutFor(WIDE_LAYOUT_MIN_WIDTH - 1, 700);
    expect(l.panel).toBeNull();
    expect(l.focusY).toBeGreaterThan(350);
  });
});
