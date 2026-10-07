import { describe, it, expect } from "vitest";
import { RIBBON_H, RIBBON_W, pulseGradientStops, pulsePaths } from "./dayPulsePaths";
import { HEAT_RAMP } from "../../styles/tokens";

const flat = Array.from({ length: 24 }, () => 2);

describe("pulsePaths", () => {
  it("maps 24 hours across the width and clamps the value to the ramp ceiling", () => {
    const { line, area } = pulsePaths(flat);
    const y = RIBBON_H - 8 - (2 / HEAT_RAMP.maxMin) * (RIBBON_H - 20);
    expect(line.startsWith(`M0,${y.toFixed(1)}`)).toBe(true);
    expect(line.endsWith(`L${RIBBON_W},${y.toFixed(1)}`)).toBe(true);
    expect(area.endsWith(`L${RIBBON_W},${RIBBON_H} L0,${RIBBON_H}Z`)).toBe(true);
    const over = pulsePaths(flat.map(() => 99));
    expect(over.line).toContain(`,${(RIBBON_H - 8 - (RIBBON_H - 20)).toFixed(1)}`);
  });
  it("breaks the line across a null hour but keeps the area on the ground there", () => {
    const withGap: (number | null)[] = [...flat]; withGap[5] = null;
    const { line } = pulsePaths(withGap);
    expect((line.match(/M/g) ?? []).length).toBe(2);
    expect(line).not.toContain("NaN");
  });
  it("renders no path for an all-null profile", () => {
    expect(pulsePaths(Array.from({ length: 24 }, () => null))).toEqual({ area: "", line: "" });
  });
});

describe("pulseGradientStops", () => {
  it("samples five hours onto the v2 ramp variables, in offset order", () => {
    const stops = pulseGradientStops(flat.map((_, h) => (h === 8 ? 6 : 0.5)));
    // Each sampled hour sits at its own x, the same place the paths draw it.
    expect(stops.map((s) => s.offset)).toEqual([3 / 23, 8 / 23, 13 / 23, 18 / 23, 1]);
    expect(stops[0].color).toBe("var(--d0)");
    expect(stops[1].color).toBe("var(--d4)");
  });
  it("treats a null sample as no delay", () => {
    expect(pulseGradientStops(Array.from({ length: 24 }, () => null))[0].color).toBe("var(--d0)");
  });
});
