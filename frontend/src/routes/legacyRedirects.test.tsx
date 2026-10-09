import type { ReactNode } from "react";
import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";
import { createMemoryRouter, RouterProvider } from "react-router-dom";
import {
  RedirectAnalysisLens,
  RedirectForecast,
  RedirectReportType,
  RedirectSaved,
  RedirectTo,
  RedirectWhere,
} from "./legacyRedirects";

function Target({ label }: { label: string }) {
  return <div>{label}</div>;
}

function go(path: string, element: ReactNode, from: string, targetPath: string) {
  const router = createMemoryRouter(
    [
      { path, element },
      { path: targetPath, element: <Target label="landed" /> },
    ],
    { initialEntries: [from] },
  );
  render(<RouterProvider router={router} />);
  expect(screen.getByText("landed")).toBeInTheDocument();
  expect(router.state.historyAction).toBe("REPLACE");
  const { pathname, search } = router.state.location;
  return { pathname, params: new URLSearchParams(search) };
}

const LENS = "agencies/:agencyId/analysis/:lens";

describe("legacy redirects", () => {
  it.each(["operations", "overview", "map"])("sends /%s to live with its filters", (seg) => {
    const loc = go(`agencies/:agencyId/${seg}`, <RedirectTo dest="live" />, `/agencies/9/${seg}?routes=50`, "agencies/:agencyId/live");
    expect(loc.pathname).toBe("/agencies/9/live");
    expect(loc.params.toString()).toBe("routes=50");
  });

  it("sends period-overview to pulse", () => {
    const loc = go(
      "agencies/:agencyId/period-overview",
      <RedirectTo dest="pulse" />,
      "/agencies/9/period-overview?from=2026-09-01",
      "agencies/:agencyId/pulse",
    );
    expect(loc.pathname).toBe("/agencies/9/pulse");
    expect(loc.params.toString()).toBe("from=2026-09-01");
  });

  it("sends the where lens with one route to that route's stops, dropping routes", () => {
    const loc = go(
      LENS,
      <RedirectAnalysisLens />,
      "/agencies/9/analysis/where?routes=50&sub_tab=marey&from=2026-09-01",
      "agencies/:agencyId/routes/:routeCode",
    );
    expect(loc.pathname).toBe("/agencies/9/routes/50");
    expect(loc.params.get("tab")).toBe("stops");
    expect(loc.params.get("sub_tab")).toBe("marey");
    expect(loc.params.get("from")).toBe("2026-09-01");
    expect(loc.params.has("routes")).toBe(false);
  });

  it("sends the where lens with several routes to the Routes list, keeping them", () => {
    const loc = go(LENS, <RedirectAnalysisLens />, "/agencies/9/analysis/where?routes=50,51", "agencies/:agencyId/routes");
    expect(loc.pathname).toBe("/agencies/9/routes");
    expect(loc.params.get("routes")).toBe("50,51");
  });

  it("sends route-analysis with one route to that route's stops", () => {
    const loc = go(
      "agencies/:agencyId/route-analysis",
      <RedirectWhere />,
      "/agencies/9/route-analysis?routes=50",
      "agencies/:agencyId/routes/:routeCode",
    );
    expect(loc.pathname).toBe("/agencies/9/routes/50");
    expect(loc.params.toString()).toBe("tab=stops");
  });

  it("sends route-analysis without a route to the Routes list", () => {
    const loc = go("agencies/:agencyId/route-analysis", <RedirectWhere />, "/agencies/9/route-analysis", "agencies/:agencyId/routes");
    expect(loc.pathname).toBe("/agencies/9/routes");
  });

  it.each([
    ["when", "time"],
    ["why", "why"],
    ["overview", "pulse"],
    ["predict", "pulse"],
    ["banana", "pulse"],
  ])("sends the %s lens to %s with its filters", (lens, dest) => {
    const loc = go(LENS, <RedirectAnalysisLens />, `/agencies/9/analysis/${lens}?from=2026-09-01`, `agencies/:agencyId/${dest}`);
    expect(loc.pathname).toBe(`/agencies/9/${dest}`);
    expect(loc.params.toString()).toBe("from=2026-09-01");
  });

  it("sends the rider lens to Routes sorted by on-time", () => {
    const loc = go(LENS, <RedirectAnalysisLens />, "/agencies/9/analysis/rider?from=2026-09-01", "agencies/:agencyId/routes");
    expect(loc.pathname).toBe("/agencies/9/routes");
    expect(loc.params.get("sort")).toBe("on_time");
    expect(loc.params.get("from")).toBe("2026-09-01");
  });

  it("sends the compare lens in agencies mode to Compare by agencies", () => {
    const loc = go(
      LENS,
      <RedirectAnalysisLens />,
      "/agencies/9/analysis/compare?mode=agencies&from=2026-09-01",
      "agencies/:agencyId/compare",
    );
    expect(loc.pathname).toBe("/agencies/9/compare");
    expect(loc.params.get("by")).toBe("agencies");
    expect(loc.params.has("mode")).toBe(false);
    expect(loc.params.get("from")).toBe("2026-09-01");
  });

  it("sends the compare lens otherwise to Compare by periods", () => {
    const loc = go(LENS, <RedirectAnalysisLens />, "/agencies/9/analysis/compare", "agencies/:agencyId/compare");
    expect(loc.params.get("by")).toBe("periods");
  });

  it("sends an analysis/:reportType bookmark to the screen hosting it", () => {
    const loc = go(LENS, <RedirectAnalysisLens />, "/agencies/9/analysis/dwell_run?routes=50", "agencies/:agencyId/why");
    expect(loc.pathname).toBe("/agencies/9/why");
    expect(loc.params.get("report")).toBe("dwell_run");
    expect(loc.params.get("routes")).toBe("50");
  });

  it("sends reports/:reportType to its Reports document", () => {
    const loc = go(
      "agencies/:agencyId/reports/:reportType",
      <RedirectReportType />,
      "/agencies/9/reports/delay_certificate?from=2026-09-01",
      "agencies/:agencyId/reports",
    );
    expect(loc.pathname).toBe("/agencies/9/reports");
    expect(loc.params.get("doc")).toBe("certificate");
    expect(loc.params.get("from")).toBe("2026-09-01");
  });

  it.each([
    ["?view=saved&from=2026-09-01", { doc: "saved", from: "2026-09-01" }],
    ["?view=reports", { doc: "council" }],
    ["?report=delay_certificate", { doc: "certificate" }],
    ["", {}],
  ])("sends saved%s to Reports with its document", (search, expected) => {
    const loc = go("agencies/:agencyId/saved", <RedirectSaved />, `/agencies/9/saved${search}`, "agencies/:agencyId/reports");
    expect(loc.pathname).toBe("/agencies/9/reports");
    expect(Object.fromEntries(loc.params)).toEqual(expected);
  });

  it("sends forecast to Time's route forecast", () => {
    const loc = go("agencies/:agencyId/forecast", <RedirectForecast />, "/agencies/9/forecast?routes=50", "agencies/:agencyId/time");
    expect(loc.pathname).toBe("/agencies/9/time");
    expect(loc.params.get("report")).toBe("route_forecast");
    expect(loc.params.get("routes")).toBe("50");
  });

  it("sends the agency network page to Compare by agencies", () => {
    const loc = go(
      "agencies/:agencyId/network",
      <RedirectTo dest="compare" extra={{ by: "agencies" }} />,
      "/agencies/9/network?from=2026-09-01",
      "agencies/:agencyId/compare",
    );
    expect(loc.pathname).toBe("/agencies/9/compare");
    expect(loc.params.get("by")).toBe("agencies");
    expect(loc.params.get("from")).toBe("2026-09-01");
  });
});
