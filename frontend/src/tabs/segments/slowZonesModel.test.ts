import { describe, expect, it } from "vitest";
import type { SlowZoneSegmentData, SlowZonesResponse } from "./slowZonesApi";
import {
  excessClass,
  observedExcess,
  segmentBounds,
  segmentFeatures,
  segmentKey,
  slowZoneSegments,
  streakFillsWindow,
} from "./slowZonesModel";

function segment(over: Partial<SlowZoneSegmentData> = {}, excess: number | null = 80, enough = true): SlowZoneSegmentData {
  return {
    from_stop_id: "S1",
    from_stop_name: "紙屋町",
    from_lon: 132.455,
    from_lat: 34.395,
    to_stop_id: "S2",
    to_stop_name: "八丁堀",
    to_lon: 132.46,
    to_lat: 34.395,
    distance_m: 459,
    routes: ["R1"],
    latest: {
      samples: 21,
      days: 1,
      run_p50_sec: excess == null ? null : 120 + excess,
      run_p85_sec: null,
      scheduled_run_sec: 120,
      excess_sec: excess,
      speed_kmh: 8.3,
      trips_per_day: 21,
      minutes_lost_per_day: 28,
      enough_samples: enough,
    },
    weekly_excess_sec: [null, 70, 80],
    slow_weeks: 2,
    ...over,
  };
}

function response(segments: SlowZoneSegmentData[], slow: [string, string][]): SlowZonesResponse {
  return {
    available: true,
    unavailable_reason: null,
    last_checked: null,
    value_basis: "feed_prediction",
    day_type: "weekday",
    time_band: "morning",
    weeks: ["2026-03-23", "2026-03-30", "2026-04-06"],
    thresholds: { excess_sec: 60, min_scheduled_sec: 60, min_weeks: 3, min_samples: 21 },
    segments,
    slow_zones: slow,
  };
}

describe("slow zones model", () => {
  it.each([
    [-10, 0],
    [0, 0],
    [1, 1],
    [30, 1],
    [31, 2],
    [60, 2],
    [61, 3],
    [120, 3],
    [121, 4],
  ])("classes an excess of %s s as %s", (excess, cls) => {
    expect(excessClass(segment({}, excess))).toBe(cls);
  });

  it("gives no class to a week observed too little or not at all", () => {
    expect(excessClass(segment({}, 300, false))).toBeNull();
    expect(excessClass(segment({ latest: null }))).toBeNull();
  });

  it("draws a straight line per placed segment and marks the slow zones", () => {
    const placed = segment();
    const other = segment({ from_stop_id: "S2", to_stop_id: "S3", to_lon: null });
    const collection = segmentFeatures(response([placed, other], [["S1", "S2"]]));
    expect(collection.features).toHaveLength(1);
    expect(collection.features[0].geometry.coordinates).toEqual([
      [132.455, 34.395],
      [132.46, 34.395],
    ]);
    expect(collection.features[0].properties).toEqual({ key: segmentKey(placed), excess_class: 3, slow: true });
  });

  it("frames every placed stop", () => {
    expect(segmentBounds([segment(), segment({ to_lon: null, from_lon: 132.5, from_lat: 34.5 })])).toEqual([
      132.455, 34.395, 132.5, 34.5,
    ]);
    expect(segmentBounds([])).toBeNull();
  });

  it("lists the slow zones in the response's order", () => {
    const a = segment();
    const b = segment({ from_stop_id: "S7", to_stop_id: "S8" });
    expect(slowZoneSegments(response([a, b], [["S7", "S8"], ["S1", "S2"]]))).toEqual([b, a]);
  });

  it("skips the weeks with too few runs in the sparkline", () => {
    expect(observedExcess(segment())).toEqual([70, 80]);
  });

  it("says a streak may have started earlier when it fills every week read", () => {
    const weeks = ["2026-03-23", "2026-03-30", "2026-04-06"];
    expect(streakFillsWindow(segment({ slow_weeks: 3 }), weeks)).toBe(true);
    expect(streakFillsWindow(segment({ slow_weeks: 2 }), weeks)).toBe(false);
  });
});
