import { describe, expect, it } from "vitest";
import { REPORT_TYPE_IDS } from "../tabs/reportTypes";
import {
  VISIBLE_LENSES,
  isLensId,
  lensHref,
  lensReportTypes,
  mergeSearch,
  reportDestination,
  reportHref,
} from "./analysisRoutes";

describe("analysisRoutes", () => {
  it("shows six lenses in stage 1, with Predict hidden", () => {
    expect(VISIBLE_LENSES).toEqual(["overview", "when", "where", "why", "rider", "compare"]);
  });

  it("recognises lens ids and rejects report types and junk", () => {
    expect(isLensId("when")).toBe(true);
    expect(isLensId("predict")).toBe(true);
    expect(isLensId("trend")).toBe(false);
    expect(isLensId(undefined)).toBe(false);
  });

  it("gives every report type exactly one destination", () => {
    for (const type of REPORT_TYPE_IDS) expect(reportDestination(type)).toBeTruthy();
    expect(reportDestination("dwell_run")).toBe("why");
    expect(reportDestination("route_forecast")).toBe("when");
    expect(reportDestination("council_summary")).toBe("saved");
    expect(reportDestination("banana")).toBe("overview");
  });

  it("lists each lens's report types in list order", () => {
    expect(lensReportTypes("when")).toEqual(["trend", "dow_weekday", "dow_weekend", "route_forecast"]);
    expect(lensReportTypes("rider")).toEqual(["ranking", "ranking_best", "on_time", "worst_5min"]);
    expect(lensReportTypes("overview")).toEqual([]);
  });

  it("merges extra params into an existing query string without duplicating keys", () => {
    expect(mergeSearch("?from=2026-08-20&mode=x", { mode: "agencies" })).toBe("?from=2026-08-20&mode=agencies");
    expect(mergeSearch("", {})).toBe("");
  });

  it("builds lens and report hrefs that carry the scope", () => {
    expect(lensHref(9, "where", "?routes=50")).toBe("/agencies/9/analysis/where?routes=50");
    expect(reportHref(9, "dwell_run", "?routes=50")).toBe("/agencies/9/analysis/why?routes=50&report=dwell_run");
    expect(reportHref("9", "delay_certificate", "")).toBe("/agencies/9/saved?report=delay_certificate");
  });

  it("sends an unknown report type to Overview without naming it", () => {
    expect(reportHref(9, "banana", "?routes=50")).toBe("/agencies/9/analysis/overview?routes=50");
    expect(reportHref(9, "constructor")).toBe("/agencies/9/analysis/overview");
    expect(reportDestination("toString")).toBe("overview");
  });
});
