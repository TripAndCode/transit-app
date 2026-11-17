import { describe, expect, it } from "vitest";
import type { SupplyAgency, SupplyStop } from "./supplyApi";
import { supplyPath } from "./supplyApi";
import {
  hourLabel,
  overGap,
  perHourClass,
  serviceClock,
  stopBounds,
  stopKey,
  supplyFeatures,
  supplySummary,
} from "./supplyModel";

function stop(over: Partial<SupplyStop>): SupplyStop {
  return {
    agency_id: 1,
    stop_id: "S1",
    stop_name: "S1前",
    lat: 34.4,
    lon: 132.4,
    departures: 6,
    per_hour: 1,
    longest_gap_min: 60,
    first_sec: 6 * 3600,
    last_sec: 22 * 3600,
    ...over,
  };
}

function agency(over: Partial<SupplyAgency>): SupplyAgency {
  return { agency_id: 1, agency_name: "A", status: "ok", static_version_id: "v", typical_day: "2026-04-06", ...over };
}

describe("supply model", () => {
  it("classes departures per hour at 1, 2 and 3 an hour", () => {
    expect([0, 0.5, 1, 1.9, 2, 2.99, 3, 8].map(perHourClass)).toEqual([0, 1, 2, 2, 3, 3, 4, 4]);
  });

  it("highlights only a gap longer than the threshold", () => {
    expect(overGap(stop({ longest_gap_min: 60 }), 60)).toBe(false);
    expect(overGap(stop({ longest_gap_min: 60.5 }), 60)).toBe(true);
  });

  it("reads service-day clocks past midnight as 24 and later", () => {
    expect(serviceClock(24 * 3600 + 20 * 60)).toBe("24:20");
    expect(serviceClock(6 * 3600 + 5 * 60 + 59)).toBe("6:05");
    expect(hourLabel(25)).toBe("25:00");
  });

  it("draws only placed stops, keyed across operators", () => {
    const features = supplyFeatures(
      [stop({}), stop({ agency_id: 2 }), stop({ stop_id: "S9", lat: null, lon: null })],
      30,
    ).features;
    expect(features.map((f) => f.properties)).toEqual([
      { key: "1:S1", per_hour_class: 2, over_gap: true },
      { key: "2:S1", per_hour_class: 2, over_gap: true },
    ]);
    expect(stopKey(stop({ agency_id: 3, stop_id: "x" }))).toBe("3:x");
  });

  it("counts the stops, those over the gap, those without a position and the operators read", () => {
    const summary = supplySummary(
      [agency({}), agency({ agency_id: 2, status: "not_built" })],
      [stop({ longest_gap_min: 90 }), stop({ stop_id: "S2", lat: null }), stop({ stop_id: "S3", longest_gap_min: 20 })],
      60,
    );
    expect(summary).toEqual({ stops: 3, overGap: 1, unplaced: 1, readAgencies: 1 });
  });

  it("bounds the placed stops", () => {
    expect(stopBounds([stop({ lat: 34, lon: 132 }), stop({ lat: 35, lon: 133 }), stop({ lat: null })])).toEqual([
      132, 34, 133, 35,
    ]);
    expect(stopBounds([stop({ lon: null })])).toBeNull();
  });

  it("asks for today when no date is chosen", () => {
    expect(supplyPath({ date: "", dayType: "weekday", fromHour: 10, toHour: 16 })).toBe(
      "/api/network/supply?day_type=weekday&from_hour=10&to_hour=16",
    );
    expect(supplyPath({ date: "2026-04-06", dayType: "saturday", fromHour: 23, toHour: 26 })).toContain(
      "date=2026-04-06",
    );
  });
});
