import { describe, it, expect } from "vitest";
import { DOWS, HOURS, SURFACE_DIM, THIN_OPACITY, bandSurface, bandThin, emptySurface, heatSurfaceDimRules, observedSurface, observedThin, ringFor, surfaceHasData } from "./heatSurfaceModel";

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

describe("observedThin", () => {
  it("marks a weekday-hour pooled from fewer than the low-confidence floor of samples", () => {
    const thin = observedThin([
      { date: "2026-10-05", hour: 8, avg_min: 2, samples: 10 },
      { date: "2026-10-12", hour: 8, avg_min: 2, samples: 10 },
      { date: "2026-10-05", hour: 9, avg_min: 2, samples: 40 },
    ]);
    expect(thin[0][8]).toBe(true);
    expect(thin[0][9]).toBe(false);
    expect(thin[0][10]).toBe(false);
  });
});

describe("bandSurface", () => {
  const observed = observedSurface([6, 8, 20, 22].map((hour) => ({ date: "2026-10-05", hour, avg_min: 1, samples: 40 })));
  it("draws a band's average across the band's hours that were observed", () => {
    // Bands come from api/types.ts BAND_RANGES: morning is 06-09, night 19-24.
    const s = bandSurface([{ dow: 1, band: "morning", expected_avg_min: 3 }, { dow: 1, band: "night", expected_avg_min: 1 }], observed);
    expect(s[0][6]).toBe(3);
    expect(s[0][8]).toBe(3);
    expect(s[0][20]).toBe(1);
    expect(s[0][22]).toBe(1);
  });
  it("leaves an hour no trip ran in empty, as the hourly profile does", () => {
    const s = bandSurface([{ dow: 1, band: "morning", expected_avg_min: 3 }], observed);
    expect(s[0][7]).toBeNull();
    expect(s[0][5]).toBeNull();
    expect(s[0][9]).toBeNull();
  });
  it("skips cells without a value or outside the week", () => {
    const s = bandSurface([{ dow: 1, band: "morning", expected_avg_min: null }, { dow: 8, band: "morning", expected_avg_min: 2 }], observed);
    expect(surfaceHasData(s)).toBe(false);
  });
  it("carries a band's low confidence onto its observed hours only", () => {
    const tuesday = observedSurface([{ date: "2026-10-06", hour: 6, avg_min: 1, samples: 40 }]);
    const thin = bandThin([{ dow: 2, band: "morning", expected_avg_min: 2, low_confidence: true }], tuesday);
    expect(thin[1][6]).toBe(true);
    expect(thin[1][7]).toBe(false); // in the band, but nothing ran: empty, not faint
    expect(thin[1][9]).toBe(false);
  });
  it("rests a thin cell above the hover dim, so a hover still narrows the surface", () => {
    expect(THIN_OPACITY).toBeGreaterThan(SURFACE_DIM);
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
    // The lowest of the surface's own dim, a linked chart's dim and the cell's
    // resting opacity, so neither dim can brighten a thin cell or the other.
    const rule = "{opacity:min(var(--focus-dim),var(--mark-opacity),.55)}";
    expect(css.split(rule).length - 1).toBe(HOURS * DOWS);
    expect(css).toContain(`.heat-surface[data-focus-hour="8"][data-focus-dow="1"] .heat-surface__cell:not([data-hour="8"]):not([data-dow="1"])${rule}`);
    expect(SURFACE_DIM).toBe(0.55);
    expect(css).not.toMatch(/transition|width|height/);
  });
  it("emptySurface is 7 × 24 nulls", () => {
    const s = emptySurface();
    expect(s).toHaveLength(7);
    expect(s.every((row) => row.length === 24 && row.every((v) => v === null))).toBe(true);
  });
});
