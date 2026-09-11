import { describe, expect, it } from "vitest";
import type { LiveTrip, RouteSummary } from "../../api/types";
import { buildCurrentRouteSummaries } from "./currentRouteStatus";

function trip(route: string, delay: number): LiveTrip {
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

function baseline(): RouteSummary {
  return {
    route_code: "12",
    service_type: "weekday",
    avg_delay_sec: 80,
    worst_delay_sec: 200,
    trips_observed: 20,
    samples: 100,
    last_seen_at: null,
    baseline_avg_sec: 60,
    baseline_p90_sec: 240,
    baseline_samples: 500,
    deviation_sec: 20,
    bucket: "normal",
    low_confidence: false,
    has_baseline: true,
  };
}

describe("buildCurrentRouteSummaries", () => {
  it("classifies active trips against the historical route baseline", () => {
    const [route] = buildCurrentRouteSummaries([trip("12", 420), trip("12", 300)], [baseline()]);
    expect(route.avg_delay_sec).toBe(360);
    expect(route.deviation_sec).toBe(300);
    expect(route.bucket).toBe("anomaly");
    expect(route.trips_observed).toBe(2);
  });

  it("keeps a delayed active route visible before a baseline exists", () => {
    const [route] = buildCurrentRouteSummaries([trip("NEW", 360)], []);
    expect(route.bucket).toBe("anomaly");
    expect(route.has_baseline).toBe(false);
  });

  it("uses the baseline for the active service type", () => {
    const weekend = { ...baseline(), service_type: "weekend", baseline_avg_sec: 300, baseline_p90_sec: 600 };
    const weekday = { ...baseline(), baseline_avg_sec: 60, baseline_p90_sec: 240 };
    const [route] = buildCurrentRouteSummaries([trip("12", 360)], [weekend, weekday]);

    expect(route.service_type).toBe("weekday");
    expect(route.baseline_avg_sec).toBe(60);
    expect(route.bucket).toBe("anomaly");
  });
});
