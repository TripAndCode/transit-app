import { describe, expect, it } from "vitest";
import type { RouteSummary } from "./types";
import { todayMeanMinutes, unusualRouteCount } from "./todayRouteFigures";

function row(route_code: string, avg_delay_sec: number, trips_observed: number, bucket: RouteSummary["bucket"] = "normal"): RouteSummary {
  return { route_code, avg_delay_sec, trips_observed, bucket } as RouteSummary;
}

describe("todayMeanMinutes", () => {
  const rows = [row("50", 120, 10), row("50", 240, 30), row("24", 60, 20), row("3", 900, 0)];

  it("weights every route's mean by its observed trips", () => {
    expect(todayMeanMinutes(rows)).toBeCloseTo((120 * 10 + 240 * 30 + 60 * 20) / 60 / 60);
  });

  it("weights one route's service types by their trips", () => {
    expect(todayMeanMinutes(rows, "50")).toBeCloseTo(3.5);
  });

  it("is unknown without an observed trip", () => {
    expect(todayMeanMinutes(rows, "3")).toBeNull();
    expect(todayMeanMinutes(undefined)).toBeNull();
  });
});

describe("unusualRouteCount", () => {
  it("counts each route running later than usual once, whatever its service types", () => {
    expect(unusualRouteCount([row("50", 0, 1, "anomaly"), row("50", 0, 1, "anomaly"), row("24", 0, 1, "anomaly"), row("3", 0, 1, "watch")])).toBe(2);
  });

  it("is zero without a summary", () => {
    expect(unusualRouteCount(undefined)).toBe(0);
  });
});
