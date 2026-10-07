import { describe, expect, it } from "vitest";
import { REPORT_TYPE_IDS } from "../tabs/reportTypes";
import {
  COMPARE_REPORT_TYPES,
  DESTINATIONS,
  ROUTES_REPORT_TYPES,
  SAVED_REPORT_TYPES,
  TIME_REPORT_TYPES,
  WHY_REPORT_TYPES,
  agencySwitchHref,
  destHref,
  mergeSearch,
  reportDestination,
  reportHref,
  routeHref,
  routesHref,
  screenParams,
} from "./destinations";

describe("destinations", () => {
  it("lists the seven destinations in rail order", () => {
    expect(DESTINATIONS).toEqual(["pulse", "routes", "time", "why", "compare", "live", "reports"]);
  });

  it("gives every report type exactly one home", () => {
    const homes = [
      ...ROUTES_REPORT_TYPES,
      ...TIME_REPORT_TYPES,
      ...WHY_REPORT_TYPES,
      ...COMPARE_REPORT_TYPES,
      ...SAVED_REPORT_TYPES,
    ];
    expect([...homes].sort()).toEqual([...REPORT_TYPE_IDS].sort());
  });

  it("builds destination hrefs that carry the scope", () => {
    expect(destHref(9, "time", "?from=2026-09-01")).toBe("/agencies/9/time?from=2026-09-01");
    expect(destHref(9, "compare", "", { by: "agencies" })).toBe("/agencies/9/compare?by=agencies");
  });

  it("puts the route in the dossier's path, not its query", () => {
    expect(routeHref(9, "50", "?routes=50&from=2026-09-01")).toBe("/agencies/9/routes/50?from=2026-09-01");
    expect(routeHref(9, "a/b", "", "stops")).toBe("/agencies/9/routes/a%2Fb?tab=stops");
    // Which report or document the list was showing says nothing about a route.
    expect(routeHref(9, "50", "?report=worst_5min&sort=ranking&doc=council&by=periods&sparse=1&dow=weekday")).toBe(
      "/agencies/9/routes/50?dow=weekday",
    );
  });

  it("opens one selected route's dossier, and the list for any other selection", () => {
    expect(routesHref(9, "?routes=50&from=2026-09-01", "stops")).toBe("/agencies/9/routes/50?from=2026-09-01&tab=stops");
    expect(routesHref(9, "?routes=50,51")).toBe("/agencies/9/routes?routes=50%2C51");
    expect(routesHref(9, "", "stops")).toBe("/agencies/9/routes");
  });

  it("names the screen each report type opens on", () => {
    expect(reportDestination("dwell_run")).toBe("why");
    expect(reportDestination("delay_certificate")).toBe("reports");
    expect(reportDestination("banana")).toBeNull();
  });

  it("sends each report type to the screen that hosts it", () => {
    expect(reportHref(9, "trend", "?routes=5")).toBe("/agencies/9/time?routes=5&report=trend");
    expect(reportHref(9, "dwell_run")).toBe("/agencies/9/why?report=dwell_run");
    expect(reportHref(9, "on_time")).toBe("/agencies/9/routes?report=on_time");
    expect(reportHref(9, "compare_ranking")).toBe("/agencies/9/compare?by=periods&report=compare_ranking");
    expect(reportHref(9, "council_summary", "?report=trend&from=2026-09-01")).toBe(
      "/agencies/9/reports?from=2026-09-01&doc=council",
    );
    expect(reportHref(9, "delay_certificate")).toBe("/agencies/9/reports?doc=certificate");
    expect(reportHref(9, "banana", "?from=2026-09-01")).toBe("/agencies/9/pulse?from=2026-09-01");
    expect(reportHref(9, "constructor")).toBe("/agencies/9/pulse");
  });

  it("keeps the screen-picking params on an agency switch", () => {
    expect(screenParams("?by=agencies&doc=saved&routes=5")).toEqual({ by: "agencies", doc: "saved" });
    expect(mergeSearch("", {})).toBe("");
  });

  it("switches agency onto the same destination, but a dossier onto the Routes list", () => {
    expect(agencySwitchHref(4, "time", "?by=x&routes=5")).toBe("/agencies/4/time?by=x");
    expect(agencySwitchHref(4, "routes/50", "?tab=stops", "from=2026-09-01")).toBe(
      "/agencies/4/routes?from=2026-09-01",
    );
    expect(agencySwitchHref(4, "compare", "?by=agencies")).toBe("/agencies/4/compare?by=agencies");
    expect(agencySwitchHref(4, "", "")).toBe("/agencies/4/pulse");
    expect(agencySwitchHref(4, "time", "", "from=2026-09-01&routes=5&stop=S1")).toBe("/agencies/4/time?from=2026-09-01");
    expect(agencySwitchHref(4, undefined, "")).toBe("/agencies/4/pulse");
  });

  it("keeps only the params that pick a screen on an agency switch", () => {
    expect(screenParams("?by=periods&report=ranking&sort=avg&doc=council&routes=1")).toEqual({ by: "periods", doc: "council" });
  });
});
