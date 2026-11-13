import { describe, it, expect } from "vitest";
import { rankedBarColumn, rankedBarColor, rankedBarShares } from "./rankedBars";

describe("rankedBarColumn", () => {
  it("picks avg_min (delay ramp) right after route_code", () => {
    expect(rankedBarColumn(["route_code", "avg_min", "p50_min", "p90_min", "samples"])).toEqual({
      index: 1,
      kind: "delay",
    });
  });

  it("picks on_time_pct (accent) right after route_code", () => {
    expect(
      rankedBarColumn(["route_code", "on_time_pct", "avg_min", "samples", "low_confidence"]),
    ).toEqual({ index: 1, kind: "accent" });
  });

  it("picks late5_count (accent) right after route_code", () => {
    expect(rankedBarColumn(["route_code", "late5_count", "avg_min", "samples"])).toEqual({
      index: 1,
      kind: "accent",
    });
  });

  it("declines a table that does not start with route_code", () => {
    expect(rankedBarColumn(["dow", "hour", "avg_min", "samples"])).toBeNull();
  });

  it("declines a route_code table whose second column is not a ranked metric", () => {
    // compare_segments(dimension=dow) -- a weekday/weekend comparison, not a ranking.
    expect(rankedBarColumn(["route_code", "heijitsu_min", "kyujitsu_min", "abs_delta", "signed_delta"])).toBeNull();
    // stop-pattern tables key on stop_sequence, not route_code.
    expect(rankedBarColumn(["stop_sequence", "stop_name", "avg_min", "samples"])).toBeNull();
  });
});

describe("rankedBarShares", () => {
  it("gives the largest value a share of 1 and scales the rest proportionally", () => {
    expect(rankedBarShares([9.4, 6.1, 4.2, 3.0, 1.2])).toEqual([1, 6.1 / 9.4, 4.2 / 9.4, 3.0 / 9.4, 1.2 / 9.4]);
  });

  it("gives a lone value a share of 1", () => {
    expect(rankedBarShares([5.2])).toEqual([1]);
  });

  it("gives zero, negative, non-numeric and missing values no bar, without producing NaN", () => {
    const shares = rankedBarShares([8, 0, -3, null, undefined, "n/a"]);
    expect(shares[0]).toBe(1);
    expect(shares.slice(1)).toEqual([null, null, null, null, null]);
    expect(shares.some((s) => typeof s === "number" && Number.isNaN(s))).toBe(false);
  });

  it("gives every row no bar when nothing shown is positive", () => {
    expect(rankedBarShares([0, -1, -2])).toEqual([null, null, null]);
  });
});

describe("rankedBarColor", () => {
  it("colours a delay metric from the delay ramp, never a fixed accent", () => {
    expect(rankedBarColor("delay", 0.5)).toBe("var(--d0)");
    expect(rankedBarColor("delay", 6)).toBe("var(--d4)");
  });

  it("colours a non-delay metric with the product accent regardless of value", () => {
    expect(rankedBarColor("accent", 95)).toBe("var(--accent)");
    expect(rankedBarColor("accent", 1)).toBe("var(--accent)");
  });
});
