// @vitest-environment node
import { describe, expect, it } from "vitest";
import type { LiveTrip } from "../../api/types";
import { meanRouteDelaySec } from "./selectedRouteDelay";

function trip(route: string | null, delay: number): LiveTrip {
  return {
    trip_id: `${route}-${delay}`,
    route_code: route,
    service_type: "weekday",
    scheduled_time: "10:00:00",
    dep_delay: delay,
    captured_at: "2026-09-11T01:00:00Z",
    stop_id: "S1",
    stop_sequence: 1,
    stop_name: "中央駅",
    stop_lat: 40,
    stop_lon: 140,
    headsign: null,
  };
}

describe("meanRouteDelaySec", () => {
  it("averages the live delay of the requested route only, rounded to whole seconds", () => {
    const rows = [trip("12", 420), trip("12", 301), trip("99", 10)];
    expect(meanRouteDelaySec(rows, "12")).toBe(361);
  });

  it("is 0 when no route is selected", () => {
    expect(meanRouteDelaySec([trip("12", 420)], null)).toBe(0);
  });

  it("is 0 when the route has no live rows", () => {
    expect(meanRouteDelaySec([trip("12", 420)], "99")).toBe(0);
  });

  it("ignores rows that carry no route code", () => {
    expect(meanRouteDelaySec([trip(null, 900), trip("12", 60)], "12")).toBe(60);
  });
});
