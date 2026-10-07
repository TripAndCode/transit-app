// @vitest-environment node
import { describe, expect, it } from "vitest";
import { niceAxis } from "./niceAxis";

describe("niceAxis", () => {
  it("widens the range to round steps a reader can use", () => {
    expect(niceAxis(0, 10.7, 6)).toEqual({ low: 0, high: 12, ticks: [0, 2, 4, 6, 8, 10, 12] });
    expect(niceAxis(0, 2.7, 6).ticks.map((v) => v.toFixed(1))).toEqual(["0.0", "0.5", "1.0", "1.5", "2.0", "2.5", "3.0"]);
  });

  it("steps below zero by the same round step", () => {
    expect(niceAxis(-1.3, 4, 6)).toEqual({ low: -2, high: 4, ticks: [-2, -1, 0, 1, 2, 3, 4] });
  });

  it("takes the next round step when widening both ends would overrun the cap", () => {
    expect(niceAxis(-19.9, 10.1, 6)).toEqual({ low: -20, high: 20, ticks: [-20, -10, 0, 10, 20] });
  });

  it("never draws more steps than asked for", () => {
    for (const low of [0, -0.4, -1.3, -6.1, -19.9]) {
      for (const high of [1, 1.7, 3.2, 7.9, 10.1, 13, 31, 64]) {
        for (const intervals of [3, 6]) {
          const { ticks } = niceAxis(low, high, intervals);
          expect(ticks.length - 1).toBeLessThanOrEqual(intervals);
          expect(ticks[0]).toBeLessThanOrEqual(low);
          expect(ticks.at(-1)).toBeGreaterThanOrEqual(high);
        }
      }
    }
  });
});
