import { describe, it, expect } from "vitest";
import {
  RELIEF_BASE_M, RELIEF_CAP_MIN, RELIEF_HEIGHT_M_PER_MIN,
  reliefFeatures, reliefHeight, reliefPaint, reliefPointsFromFrame, reliefPointsFromLive, reliefTweenMs, tweenFeatures,
} from "./reliefLayer";
import { severityStepColors } from "../../styles/tokens";
import type { LiveTrip, TimelineFrame } from "../../api/types";

const P = (delay_min: number) => ({ stop_id: "S1", lon: 132.4585, lat: 34.397, delay_min });

describe("reliefHeight", () => {
  it("is delay × 55 m plus the base, capped at 5.5 minutes", () => {
    expect(reliefHeight(0)).toBe(RELIEF_BASE_M);
    expect(reliefHeight(2)).toBe(2 * RELIEF_HEIGHT_M_PER_MIN + RELIEF_BASE_M);
    expect(reliefHeight(9)).toBe(RELIEF_CAP_MIN * RELIEF_HEIGHT_M_PER_MIN + RELIEF_BASE_M);
  });
  it("clamps an early stop to the base height, never a negative extrusion", () => {
    expect(reliefHeight(-3)).toBe(RELIEF_BASE_M);
  });
});

describe("reliefFeatures", () => {
  it("draws a closed ~20 m half-side square around each stop, carrying delay and height", () => {
    const fc = reliefFeatures([P(2)]);
    expect(fc.features).toHaveLength(1);
    const ring = fc.features[0].geometry.coordinates[0];
    expect(ring).toHaveLength(5);
    expect(ring[0]).toEqual(ring[4]);
    const lons = ring.map((c) => c[0]);
    const lats = ring.map((c) => c[1]);
    // 20 m at 34.4°N: ≈ 0.000218° of longitude, ≈ 0.000181° of latitude.
    expect(Math.max(...lons) - Math.min(...lons)).toBeCloseTo(2 * 20 / (111_320 * Math.cos((34.397 * Math.PI) / 180)), 6);
    expect(Math.max(...lats) - Math.min(...lats)).toBeCloseTo(2 * 20 / 110_574, 6);
    expect(fc.features[0].properties).toEqual({ stop_id: "S1", delay_min: 2, h: reliefHeight(2) });
  });
  it("skips points without finite coordinates", () => {
    expect(reliefFeatures([{ ...P(1), lon: Number.NaN }]).features).toEqual([]);
  });
});

describe("reliefPaint", () => {
  it("colours by the shared severity ramp and extrudes `h` from the ground, with no paint transition to ease data-driven values", () => {
    const paint = reliefPaint();
    expect(paint["fill-extrusion-color"]).toEqual(["step", ["get", "delay_min"], ...severityStepColors()]);
    expect(paint["fill-extrusion-height"]).toEqual(["get", "h"]);
    expect(paint["fill-extrusion-base"]).toBe(0);
    expect(Object.keys(paint).some((k) => k.endsWith("-transition"))).toBe(false);
  });
});

describe("reliefTweenMs", () => {
  it("is the whole cross-fade outside playback, and ends well inside a frame's dwell during it", () => {
    expect(reliefTweenMs(600, null)).toBe(600);
    expect(reliefTweenMs(600, 450)).toBeLessThanOrEqual(450 * 0.6);
    expect(reliefTweenMs(600, 900)).toBeLessThanOrEqual(600);
    expect(reliefTweenMs(0, 450)).toBe(0);
  });
});

describe("reliefPointsFromLive", () => {
  const row = (o: Partial<LiveTrip>): LiveTrip => ({
    trip_id: "t", route_code: "3", service_type: null, scheduled_time: null, dep_delay: 60, captured_at: "2026-10-03T00:00:00Z",
    stop_id: "S1", stop_sequence: 1, stop_name: "A", stop_lat: 34.4, stop_lon: 132.4, headsign: null, ...o,
  });
  it("pools every reading at a stop into one column with the mean delay in minutes", () => {
    const points = reliefPointsFromLive([row({ dep_delay: 60 }), row({ trip_id: "u", dep_delay: 180 }), row({ trip_id: "v", stop_id: "S2", stop_lat: 34.5 })]);
    expect(points).toEqual([
      { stop_id: "S1", lon: 132.4, lat: 34.4, delay_min: 2 },
      { stop_id: "S2", lon: 132.4, lat: 34.5, delay_min: 1 },
    ]);
  });
  it("drops rows with no position or no stop id", () => {
    expect(reliefPointsFromLive([row({ stop_lat: null }), row({ stop_id: null })])).toEqual([]);
  });
});

describe("reliefPointsFromFrame", () => {
  it("maps a playback frame's points straight through, and an absent frame to nothing", () => {
    const frame: TimelineFrame = { t: "08:00", mean_delay_min: 3, samples: 4, points: [{ stop_id: "S9", stop_name: null, lon: 1, lat: 2, avg_delay_min: 3, samples: 4 }] };
    expect(reliefPointsFromFrame(frame)).toEqual([{ stop_id: "S9", lon: 1, lat: 2, delay_min: 3 }]);
    expect(reliefPointsFromFrame(undefined)).toEqual([]);
  });
});

describe("tweenFeatures", () => {
  const at = (stop_id: string, delay_min: number) => ({ stop_id, lon: 132.4585, lat: 34.397, delay_min });
  const props = (fc: ReturnType<typeof reliefFeatures>) => fc.features.map((f) => f.properties);

  it("runs each surviving stop's height and delay from the previous reading to the new one", () => {
    const prev = reliefFeatures([at("S1", 1)]);
    const next = reliefFeatures([at("S1", 3)]);
    expect(props(tweenFeatures(prev, next, 0))).toEqual(props(prev));
    expect(props(tweenFeatures(prev, next, 1))).toEqual(props(next));
    const mid = tweenFeatures(prev, next, 0.5).features[0].properties;
    expect(mid.delay_min).toBeCloseTo(2, 9);
    expect(mid.h).toBeCloseTo((reliefHeight(1) + reliefHeight(3)) / 2, 9);
  });

  it("raises a newly reporting stop from the ground tile and drops a stop that stopped reporting", () => {
    const prev = reliefFeatures([at("GONE", 4)]);
    const next = reliefFeatures([at("NEW", 2)]);
    const start = tweenFeatures(prev, next, 0);
    expect(start.features.map((f) => f.properties.stop_id)).toEqual(["NEW"]);
    expect(start.features[0].properties).toEqual({ stop_id: "NEW", delay_min: 0, h: RELIEF_BASE_M });
    expect(start.features[0].geometry).toBe(next.features[0].geometry);
  });
});
