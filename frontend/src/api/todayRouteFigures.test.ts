import { describe, expect, it } from "vitest";
import type { RouteSummary } from "./types";
import { todayMeanMinutes, unusualRouteCount } from "./todayRouteFigures";

function row(route_code: string, avg_delay_sec: number, trips_observed: number, bucket: RouteSummary["bucket"] = "normal"): RouteSummary {
  return { route_code, avg_delay_sec, trips_observed, bucket } as RouteSummary;
}

describe("RouteSummary", () => {
  it("is one route, naming no timetable service id", () => {
    const summary: RouteSummary = {
      route_code: "50",
      // @ts-expect-error -- the server pools every service id a route ran under and sends none.
      service_type: "11_weekday",
      avg_delay_sec: 0,
      worst_delay_sec: 0,
      trips_observed: 1,
      samples: 1,
      last_seen_at: null,
      baseline_avg_sec: null,
      baseline_p90_sec: null,
      baseline_samples: null,
      deviation_sec: null,
      bucket: "no_baseline",
      low_confidence: true,
      has_baseline: false,
    };
    expect(summary.route_code).toBe("50");
  });
});

describe("todayMeanMinutes", () => {
  const rows = [row("50", 210, 40), row("24", 60, 20), row("3", 900, 0)];

  it("weights every route's mean by its observed trips", () => {
    expect(todayMeanMinutes(rows)).toBeCloseTo((210 * 40 + 60 * 20) / 60 / 60);
  });

  it("reads one route's mean from its row", () => {
    expect(todayMeanMinutes(rows, "50")).toBeCloseTo(3.5);
  });

  it("is unknown without an observed trip", () => {
    expect(todayMeanMinutes(rows, "3")).toBeNull();
    expect(todayMeanMinutes(rows, "missing")).toBeNull();
    expect(todayMeanMinutes(undefined)).toBeNull();
  });
});

describe("unusualRouteCount", () => {
  it("counts the routes running later than usual", () => {
    expect(unusualRouteCount([row("50", 0, 1, "anomaly"), row("24", 0, 1, "anomaly"), row("3", 0, 1, "watch")])).toBe(2);
  });

  it("is zero without a summary", () => {
    expect(unusualRouteCount(undefined)).toBe(0);
  });
});
