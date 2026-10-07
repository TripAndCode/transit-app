import { describe, it, expect } from "vitest";
import { deltaFor, orderByPeriod, otherPeriod, parseCompareRows } from "./compareBarMath";

const rows = parseCompareRows([["3", 3.4, "2.7", 0.7, 0.7], ["12", 3.8, 3.1, 0.7, 0.7], ["885", null, 1.1, "", ""], ["2", 2.2, 3.5, 1.3, 1.3]]);

describe("parseCompareRows", () => {
  it("reads numbers and numeric strings, and keeps an absent half as null", () => {
    expect(rows[0]).toEqual({ route_code: "3", weekday: 3.4, weekend: 2.7 });
    expect(rows[2]).toEqual({ route_code: "885", weekday: null, weekend: 1.1 });
  });
});

describe("orderByPeriod", () => {
  it("sorts worst-first by the chosen period, nulls last, ties by route code", () => {
    expect(orderByPeriod(rows, "weekday").map((r) => r.route_code)).toEqual(["12", "3", "2", "885"]);
    expect(orderByPeriod(rows, "weekend").map((r) => r.route_code)).toEqual(["2", "12", "3", "885"]);
    expect(orderByPeriod(parseCompareRows([["b", 1, 1, 0, 0], ["a", 1, 1, 0, 0]]), "weekday").map((r) => r.route_code)).toEqual(["a", "b"]);
  });
  it("does not mutate its input", () => {
    const before = rows.map((r) => r.route_code);
    orderByPeriod(rows, "weekend");
    expect(rows.map((r) => r.route_code)).toEqual(before);
  });
});

describe("deltaFor / otherPeriod", () => {
  it("delta is the shown period minus the ghost to the printed tenth, null when either is missing", () => {
    expect(deltaFor(rows[0], "weekday")).toBe(0.7);
    expect(deltaFor(rows[0], "weekend")).toBe(-0.7);
    expect(deltaFor(rows[3], "weekend")).toBe(1.3);
    expect(deltaFor({ route_code: "7", weekday: 1.24, weekend: 1.2 }, "weekend")).toBe(0);
    // The gap between the printed 1.5 and 1.3, the same size from either side.
    expect(deltaFor({ route_code: "8", weekday: 1.5, weekend: 1.25 }, "weekday")).toBe(0.2);
    expect(deltaFor({ route_code: "8", weekday: 1.5, weekend: 1.25 }, "weekend")).toBe(-0.2);
    expect(deltaFor(rows[2], "weekday")).toBeNull();
    expect(otherPeriod("weekday")).toBe("weekend");
  });
});
