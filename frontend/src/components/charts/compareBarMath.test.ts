import { describe, it, expect } from "vitest";
import { COMPARE_AXIS_MAX_MIN, barScale, deltaFor, orderByPeriod, otherPeriod, parseCompareRows } from "./compareBarMath";

const rows = parseCompareRows([["3", 3.4, "2.7", 0.7, 0.7], ["12", 3.8, 3.1, 0.7, 0.7], ["885", null, 1.1, "", ""], ["2", 2.2, 2.6, 0.4, -0.4]]);

describe("parseCompareRows", () => {
  it("reads numbers and numeric strings, and keeps an absent half as null", () => {
    expect(rows[0]).toEqual({ route_code: "3", weekday: 3.4, weekend: 2.7 });
    expect(rows[2]).toEqual({ route_code: "885", weekday: null, weekend: 1.1 });
  });
});

describe("orderByPeriod", () => {
  it("sorts worst-first by the chosen period, nulls last, ties by route code", () => {
    expect(orderByPeriod(rows, "weekday").map((r) => r.route_code)).toEqual(["12", "3", "2", "885"]);
    expect(orderByPeriod(rows, "weekend").map((r) => r.route_code)).toEqual(["12", "3", "2", "885"]);
    expect(orderByPeriod(parseCompareRows([["b", 1, 1, 0, 0], ["a", 1, 1, 0, 0]]), "weekday").map((r) => r.route_code)).toEqual(["a", "b"]);
  });
  it("does not mutate its input", () => {
    const before = rows.map((r) => r.route_code);
    orderByPeriod(rows, "weekend");
    expect(rows.map((r) => r.route_code)).toEqual(before);
  });
});

describe("barScale / deltaFor / otherPeriod", () => {
  it("scales against the fixed 6-minute axis and clamps", () => {
    expect(barScale(3)).toBe(0.5);
    expect(barScale(9)).toBe(1);
    expect(barScale(-1)).toBe(0);
    expect(barScale(null)).toBe(0);
    expect(COMPARE_AXIS_MAX_MIN).toBe(6);
  });
  it("delta is the shown period minus the ghost, null when either is missing", () => {
    expect(deltaFor(rows[0], "weekday")).toBeCloseTo(0.7, 6);
    expect(deltaFor(rows[0], "weekend")).toBeCloseTo(-0.7, 6);
    expect(deltaFor(rows[2], "weekday")).toBeNull();
    expect(otherPeriod("weekday")).toBe("weekend");
  });
});
