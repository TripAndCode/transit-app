import { describe, expect, it } from "vitest";
import type { SupplyAgency, SupplyMapResponse } from "./supplyApi";
import { supplyPath, supplyStopPath } from "./supplyApi";
import {
  hourLabel,
  mapStops,
  overGapExpression,
  perHourClassExpression,
  serviceClock,
  stopBounds,
  stopKey,
  supplyFeatures,
  supplySummary,
  type MapStop,
} from "./supplyModel";

function stop(over: Partial<MapStop>): MapStop {
  return { agencyId: 1, stopId: "S1", lat: 34.4, lon: 132.4, departures: 6, longestGapMin: 60, ...over };
}

function agency(over: Partial<SupplyAgency>): SupplyAgency {
  return {
    agency_id: 1,
    agency_name: "A",
    status: "ok",
    static_version_id: "v",
    typical_day: "2026-04-06",
    stops: null,
    ...over,
  };
}

function response(agencies: SupplyAgency[]): SupplyMapResponse {
  return { date: "2026-04-06", day_type: "weekday", from_hour: 10, to_hour: 16, agencies };
}

describe("supply model", () => {
  it("unpacks each OK operator's columns into stops", () => {
    const stops = mapStops(
      response([
        agency({
          stops: {
            stop_id: ["S1", "S2"],
            lat: [34.4, null],
            lon: [132.4, null],
            departures: [6, 0],
            longest_gap_min: [60, 360],
          },
        }),
        agency({ agency_id: 2, status: "not_built" }),
      ]),
    );
    expect(stops).toEqual([
      { agencyId: 1, stopId: "S1", lat: 34.4, lon: 132.4, departures: 6, longestGapMin: 60 },
      { agencyId: 1, stopId: "S2", lat: null, lon: null, departures: 0, longestGapMin: 360 },
    ]);
    expect(mapStops(undefined)).toEqual([]);
  });

  it("classes departures per hour over the band at 1, 2 and 3 an hour, none apart", () => {
    expect(perHourClassExpression(6, [0, 1, 2, 3, 4])).toEqual([
      "case",
      ["==", ["get", "departures"], 0],
      0,
      ["step", ["/", ["get", "departures"], 6], 1, 1, 2, 2, 3, 3, 4],
    ]);
  });

  it("highlights only a gap longer than the threshold", () => {
    expect(overGapExpression(60)).toEqual([">", ["get", "gap"], 60]);
    const summary = supplySummary(response([]), [stop({ longestGapMin: 60 }), stop({ longestGapMin: 60.5 })], 60);
    expect(summary.overGap).toBe(1);
  });

  it("reads service-day clocks past midnight as 24 and later", () => {
    expect(serviceClock(24 * 3600 + 20 * 60)).toBe("24:20");
    expect(serviceClock(6 * 3600 + 5 * 60 + 59)).toBe("6:05");
    expect(hourLabel(25)).toBe("25:00");
  });

  it("draws only placed stops, keyed across operators, with their raw figures", () => {
    const features = supplyFeatures([stop({}), stop({ agencyId: 2 }), stop({ stopId: "S9", lat: null, lon: null })]).features;
    expect(features.map((f) => f.properties)).toEqual([
      { key: "1:S1", departures: 6, gap: 60 },
      { key: "2:S1", departures: 6, gap: 60 },
    ]);
    expect(stopKey({ agencyId: 3, stopId: "x" })).toBe("3:x");
  });

  it("counts the stops, those over the gap, those without a position and the operators read", () => {
    const summary = supplySummary(
      response([agency({}), agency({ agency_id: 2, status: "not_built" })]),
      [stop({ longestGapMin: 90 }), stop({ stopId: "S2", lat: null }), stop({ stopId: "S3", longestGapMin: 20 })],
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

  it("asks for today when no date is chosen, and names the stop it opens", () => {
    const query = { date: "", dayType: "weekday", fromHour: 10, toHour: 16 } as const;
    expect(supplyPath(query)).toBe("/api/network/supply?day_type=weekday&from_hour=10&to_hour=16");
    expect(supplyPath({ ...query, date: "2026-04-06" })).toContain("date=2026-04-06");
    expect(supplyStopPath(query, 3, "S 1")).toBe(
      "/api/network/supply/stop?day_type=weekday&from_hour=10&to_hour=16&agency_id=3&stop_id=S+1",
    );
  });
});
