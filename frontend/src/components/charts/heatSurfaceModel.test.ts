import { describe, it, expect } from "vitest";
import { DOWS, HOURS, SURFACE_DIM, emptySurface, forecastSurface, heatSurfaceDimRules, observedSurface, ringFor, surfaceHasData } from "./heatSurfaceModel";

describe("observedSurface", () => {
  it("pools cells of the same weekday and hour, weighted by samples", () => {
    // 2026-10-05 and 2026-10-12 are Mondays.
    const s = observedSurface([
      { date: "2026-10-05", hour: 8, avg_min: 2, samples: 10 },
      { date: "2026-10-12", hour: 8, avg_min: 4, samples: 30 },
      { date: "2026-10-11", hour: 8, avg_min: 1, samples: 5 }, // Sunday
    ]);
    expect(s[0][8]).toBeCloseTo(3.5, 6);
    expect(s[6][8]).toBe(1);
    expect(s[0][9]).toBeNull();
    expect(surfaceHasData(s)).toBe(true);
  });
  it("prefers the exact raw-seconds total when the cell carries it", () => {
    const s = observedSurface([{ date: "2026-10-05", hour: 8, avg_min: 2, samples: 10, sum_delay_sec: 1800 }]);
    expect(s[0][8]).toBe(3);
  });
  it("ignores empty cells and hours outside the day", () => {
    const s = observedSurface([
      { date: "2026-10-05", hour: 8, avg_min: null, samples: 0 },
      { date: "2026-10-05", hour: 9, avg_min: 2, samples: 0 },
      { date: "2026-10-05", hour: 24, avg_min: 2, samples: 10 },
    ]);
    expect(s[0][8]).toBeNull();
    expect(s[0][9]).toBeNull();
    expect(surfaceHasData(s)).toBe(false);
  });
});

describe("forecastSurface", () => {
  it("spreads a band's expected delay over every hour of the band", () => {
    // Bands come from api/types.ts BAND_RANGES: morning is 06-09, night 19-24.
    const s = forecastSurface([{ dow: 1, band: "morning", expected_avg_min: 3 }, { dow: 7, band: "night", expected_avg_min: 1 }]);
    expect(s[0][5]).toBeNull();
    expect(s[0][6]).toBe(3);
    expect(s[0][8]).toBe(3);
    expect(s[0][9]).toBeNull();
    expect(s[6][19]).toBe(1);
    expect(s[6][23]).toBe(1);
  });
  it("skips cells without an expectation or outside the week", () => {
    const s = forecastSurface([{ dow: 1, band: "morning", expected_avg_min: null }, { dow: 8, band: "morning", expected_avg_min: 2 }]);
    expect(surfaceHasData(s)).toBe(false);
  });
});

describe("ringFor", () => {
  it("rings by the shared thresholds, nothing for a quiet or empty cell", () => {
    expect(ringFor(null)).toBe("0px");
    expect(ringFor(2.9)).toBe("0px");
    expect(ringFor(3)).toBe("1px");
    expect(ringFor(5)).toBe("2px");
  });
});

describe("heatSurfaceDimRules", () => {
  it("emits one rule per (hour, weekday) that dims every cell in neither that column nor that row", () => {
    const css = heatSurfaceDimRules();
    expect((css.match(/\{opacity:\.55\}/g) ?? []).length).toBe(HOURS * DOWS);
    expect(css).toContain(`.heat-surface[data-focus-hour="8"][data-focus-dow="1"] .heat-surface__cell:not([data-hour="8"]):not([data-dow="1"]){opacity:.55}`);
    expect(SURFACE_DIM).toBe(0.55);
    expect(css).not.toMatch(/transition|width|height/);
  });
  it("emptySurface is 7 × 24 nulls", () => {
    const s = emptySurface();
    expect(s).toHaveLength(7);
    expect(s.every((row) => row.length === 24 && row.every((v) => v === null))).toBe(true);
  });
});
