import { describe, expect, it } from "vitest";
import { DESTINATIONS, ROUTES_REPORT_TYPES, TIME_REPORT_TYPES } from "../routes/destinations";
import { RAIL_STOPS, currentStop, railPlace } from "./railStops";

describe("RAIL_STOPS", () => {
  it("gives only the screens that switch between views any stops", () => {
    const withStops = DESTINATIONS.filter((dest) => RAIL_STOPS[dest] != null);
    expect(withStops).toEqual(["routes", "time", "compare", "reports"]);
  });

  it("opens each report of Routes and Time by its `report` param, in the screen's own order", () => {
    expect(RAIL_STOPS.routes?.map((stop) => stop.extra)).toEqual(ROUTES_REPORT_TYPES.map((report) => ({ report })));
    expect(RAIL_STOPS.time?.map((stop) => stop.extra)).toEqual(TIME_REPORT_TYPES.map((report) => ({ report })));
  });

  it("opens Compare's boards by `by` and Reports' views by `doc`", () => {
    expect(RAIL_STOPS.compare?.map((stop) => stop.extra)).toEqual([{ by: "periods" }, { by: "agencies" }]);
    expect(RAIL_STOPS.reports?.map((stop) => stop.extra)).toEqual([{}, { doc: "saved" }, { doc: "council" }]);
  });
});

describe("currentStop", () => {
  it.each([
    ["routes", "report=on_time", "on_time"],
    ["routes", "sort=worst_5min", "worst_5min"],
    ["routes", "report=trend", "ranking"],
    ["routes", "", "ranking"],
    ["time", "report=route_forecast", "route_forecast"],
    ["time", "", "trend"],
    ["compare", "by=agencies", "agencies"],
    ["compare", "by=anything", "periods"],
    ["reports", "doc=saved", "saved"],
    ["reports", "doc=certificate", "reports"],
    ["reports", "report=council_summary", "reports"],
    ["reports", "", "summary"],
    ["pulse", "report=on_time", null],
  ] as const)("on %s?%s is %s", (dest, search, stop) => {
    expect(currentStop(dest, search)).toBe(stop);
  });
});

describe("railPlace", () => {
  it("names a destination's own page and the stop it shows", () => {
    expect(railPlace("/agencies/8/time?from=2026-06-01&report=dow_weekend")).toEqual({ dest: "time", stop: "dow_weekend" });
  });

  it("puts a route dossier under Routes, at none of its stops", () => {
    expect(railPlace("/agencies/8/routes/50?routes=50")).toEqual({ dest: "routes", stop: null });
  });

  it("places Ask and pages off the rail nowhere", () => {
    expect(railPlace("/agencies/8/ask")).toBeNull();
    expect(railPlace("/help")).toBeNull();
  });
});
