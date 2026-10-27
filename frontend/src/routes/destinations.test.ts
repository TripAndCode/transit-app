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
  compareMode,
  mergeSearch,
  pickReport,
  reportDestination,
  reportHref,
  routeHref,
  routesHref,
  reportsView,
  routeTableView,
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
    expect(routeHref(9, "a/b")).toBe("/agencies/9/routes/a%2Fb");
    // Which report or document the list was showing says nothing about a route.
    expect(routeHref(9, "50", "?report=worst_5min&sort=ranking&doc=council&by=periods&sparse=1&period=weekend&order=best&dow=weekday")).toBe(
      "/agencies/9/routes/50?dow=weekday",
    );
  });

  it("opens one selected route's dossier, and the list for any other selection", () => {
    expect(routesHref(9, "?routes=50&from=2026-09-01")).toBe("/agencies/9/routes/50?from=2026-09-01");
    expect(routesHref(9, "?routes=50,51")).toBe("/agencies/9/routes?routes=50%2C51");
    expect(routesHref(9, "")).toBe("/agencies/9/routes");
  });

  it("names the screen each report type opens on", () => {
    expect(reportDestination("dwell_run")).toBe("why");
    expect(reportDestination("delay_certificate")).toBe("reports");
    expect(reportDestination("banana")).toBeNull();
  });

  it("sends each report type to the screen that hosts it", () => {
    expect(reportHref(9, "trend", "?routes=5")).toBe("/agencies/9/time?routes=5&report=trend");
    expect(reportHref(9, "dwell_run")).toBe("/agencies/9/why?report=dwell_run");
    expect(reportHref(9, "on_time")).toBe("/agencies/9/routes?sort=on_time");
    expect(reportHref(9, "ranking_best", "?from=2026-09-01")).toBe("/agencies/9/routes?from=2026-09-01&sort=avg&order=best");
    // A previous report or sort gives way to the report's own; a former
    // on-time link's order still picks the same end.
    expect(reportHref(9, "ranking", "?report=on_time&sort=late5_share")).toBe("/agencies/9/routes?sort=avg");
    expect(reportHref(9, "on_time", "?order=best&from=2026-09-01")).toBe("/agencies/9/routes?order=best&from=2026-09-01&sort=on_time");
    // Beside any other list, an order is a leftover from the on-time list.
    expect(reportHref(9, "ranking", "?order=best&from=2026-09-01")).toBe("/agencies/9/routes?from=2026-09-01&sort=avg");
    expect(reportHref(9, "worst_5min", "?order=best")).toBe("/agencies/9/routes?sort=late5_count");
    expect(reportHref(9, "worst_5min")).toBe("/agencies/9/routes?sort=late5_count");
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
    expect(agencySwitchHref(4, "routes/50", "?sub_tab=marey", "from=2026-09-01")).toBe(
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

describe("the view a screen opens", () => {
  it("opens the requested report only where the screen hosts it", () => {
    expect(pickReport("route_forecast", TIME_REPORT_TYPES)).toBe("route_forecast");
    expect(pickReport("on_time", TIME_REPORT_TYPES)).toBe("trend");
  });

  it("falls back to a hosted default, else to the first report", () => {
    expect(pickReport(null, TIME_REPORT_TYPES, "dow_weekend")).toBe("dow_weekend");
    expect(pickReport(null, TIME_REPORT_TYPES, "ranking")).toBe("trend");
    expect(pickReport(null, [])).toBeNull();
  });

  it.each([
    ["", "avg", "worst"],
    ["sort=late5_share", "late5_share", "worst"],
    ["sort=on_time&order=best", "on_time", "best"],
    // The route lists the table replaced open it sorted the way they were.
    ["report=ranking", "avg", "worst"],
    ["report=ranking_best", "avg", "best"],
    ["report=on_time&order=best", "on_time", "best"],
    ["report=worst_5min", "late5_count", "worst"],
    ["sort=worst_5min", "late5_count", "worst"],
    ["sort=ranking_best", "avg", "best"],
    // `report` picked the list over `sort`.
    ["report=on_time&sort=avg", "on_time", "worst"],
    ["report=trend&sort=banana&order=sideways", "avg", "worst"],
    // An order turns around only the on-time list, the one list that had
    // the choice; beside any other it is a leftover from a visit to that one.
    ["report=on_time&order=best", "on_time", "best"],
    ["report=ranking&order=best", "avg", "worst"],
    ["report=worst_5min&order=best", "late5_count", "worst"],
    ["report=ranking_best&order=worst", "avg", "best"],
    ["sort=worst_5min&order=best", "late5_count", "worst"],
    ["order=best", "avg", "worst"],
    // Beside a table sort it was chosen for the table.
    ["sort=late5_count&order=best", "late5_count", "best"],
  ] as const)("opens the Routes table for ?%s sorted by %s, %s first", (search, sort, order) => {
    expect(routeTableView(search)).toEqual({ sort, order });
  });

  it("compares agencies only for by=agencies", () => {
    expect(compareMode("agencies")).toBe("agencies");
    expect(compareMode("periods")).toBe("periods");
    expect(compareMode(null)).toBe("periods");
  });

  it("opens Reports' documents for an export document, saved analyses for doc=saved, else the summary", () => {
    expect(reportsView(null, "delay_certificate")).toBe("reports");
    expect(reportsView("council", null)).toBe("reports");
    expect(reportsView("saved", null)).toBe("saved");
    expect(reportsView(null, "ranking")).toBe("summary");
  });
});
