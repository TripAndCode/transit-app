// @vitest-environment node
import { describe, expect, it } from "vitest";
import type { LiveTrip } from "../../api/types";
import { nextBoundaryMs } from "./staleness";

const NOW = Date.parse("2026-09-11T01:10:00Z");

function trip(over: Partial<LiveTrip>): LiveTrip {
  return {
    trip_id: "T1",
    route_code: "R1",
    service_type: "weekday",
    scheduled_time: "10:00:00",
    dep_delay: 0,
    captured_at: "2026-09-11T01:10:00Z",
    stop_id: "S1",
    stop_sequence: 1,
    stop_name: "中央駅",
    stop_lat: 40,
    stop_lon: 140,
    headsign: null,
    ...over,
  };
}

describe("nextBoundaryMs", () => {
  it("returns null when there are no rows", () => {
    expect(nextBoundaryMs([], NOW)).toBeNull();
  });

  it("returns the 10-minute exit boundary for a freshly captured row", () => {
    // captured exactly at NOW: leaves the live-rows window at NOW + 10min,
    // and also crosses the 2-minute "normal -> delayed" freshness boundary
    // first at NOW + 2min.
    const row = trip({ captured_at: new Date(NOW).toISOString() });
    expect(nextBoundaryMs([row], NOW)).toBe(NOW + 2 * 60_000);
  });

  it("returns the exit boundary once the 2-minute freshness boundary is already past", () => {
    // captured 3 minutes before NOW: the 2-minute freshness boundary
    // (captured + 2min) is already behind `now`, so the next one is the
    // 10-minute exit boundary (captured + 10min).
    const capturedMs = NOW - 3 * 60_000;
    const row = trip({ captured_at: new Date(capturedMs).toISOString() });
    expect(nextBoundaryMs([row], NOW)).toBe(capturedMs + 10 * 60_000);
  });

  it("returns null once every boundary for every row is already past", () => {
    const capturedMs = NOW - 11 * 60_000; // both boundaries already crossed
    const row = trip({ captured_at: new Date(capturedMs).toISOString() });
    expect(nextBoundaryMs([row], NOW)).toBeNull();
  });

  it("picks the earliest upcoming boundary across multiple rows", () => {
    const soon = trip({ captured_at: new Date(NOW - 9 * 60_000).toISOString() }); // exits in 1 min
    const later = trip({ captured_at: new Date(NOW).toISOString() }); // next boundary in 2 min
    expect(nextBoundaryMs([soon, later], NOW)).toBe(NOW - 9 * 60_000 + 10 * 60_000);
  });

  it("ignores a row with an unparseable captured_at", () => {
    const bad = trip({ captured_at: "not-a-date" });
    expect(nextBoundaryMs([bad], NOW)).toBeNull();
  });
});
