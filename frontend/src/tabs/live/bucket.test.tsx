import { describe, it, expect } from "vitest";
import { groupByBucket, BUCKET_ORDER } from "./bucket";
import type { RouteSummary } from "../../api/types";

function r(route_code: string, bucket: RouteSummary["bucket"], deviation_sec: number | null): RouteSummary {
  return {
    route_code, service_type: "weekday", avg_delay_sec: 0, worst_delay_sec: 0,
    trips_observed: 1, samples: 50, last_seen_at: null,
    baseline_avg_sec: null, baseline_p90_sec: null, baseline_samples: null, deviation_sec, bucket,
    low_confidence: false, has_baseline: bucket !== "no_baseline",
  };
}

describe("groupByBucket", () => {
  it("orders buckets anomaly→watch→normal→no_baseline and sorts within by deviation desc", () => {
    const groups = groupByBucket([
      r("N1", "normal", 10),
      r("A1", "anomaly", 100),
      r("A2", "anomaly", 300),
      r("NB", "no_baseline", null),
    ]);
    expect(groups.map((g) => g.bucket)).toEqual(["anomaly", "watch", "normal", "no_baseline"]);
    const anomaly = groups.find((g) => g.bucket === "anomaly")!;
    expect(anomaly.routes.map((x) => x.route_code)).toEqual(["A2", "A1"]); // 300 before 100
  });

  it("includes empty buckets so the UI can show zero counts consistently", () => {
    const groups = groupByBucket([]);
    expect(groups.map((g) => g.bucket)).toEqual(BUCKET_ORDER);
    expect(groups.every((g) => g.routes.length === 0)).toBe(true);
  });
});
