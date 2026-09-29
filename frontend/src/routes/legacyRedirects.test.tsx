import type { ReactNode } from "react";
import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";
import { createMemoryRouter, RouterProvider } from "react-router-dom";
import {
  RedirectToLive,
  RedirectToLens,
  RedirectReportsToSaved,
  RedirectReportTypeToLens,
  RedirectForecastToWhen,
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
  return router.state.location;
}

describe("legacy redirects", () => {
  it.each(["operations", "overview", "map"])("sends /%s to live with its filters", (seg) => {
    const loc = go(`agencies/:agencyId/${seg}`, <RedirectToLive />, `/agencies/9/${seg}?routes=50`, "agencies/:agencyId/live");
    expect(loc.pathname).toBe("/agencies/9/live");
    expect(loc.search).toBe("?routes=50");
  });

  it("sends period-overview to the overview lens", () => {
    const loc = go(
      "agencies/:agencyId/period-overview",
      <RedirectToLens lens="overview" />,
      "/agencies/9/period-overview?from=2026-08-20",
      "agencies/:agencyId/analysis/:lens",
    );
    expect(loc.pathname).toBe("/agencies/9/analysis/overview");
    expect(loc.search).toBe("?from=2026-08-20");
  });

  it("sends route-analysis to the where lens, keeping the route and sub-tab", () => {
    const loc = go(
      "agencies/:agencyId/route-analysis",
      <RedirectToLens lens="where" />,
      "/agencies/9/route-analysis?routes=50&sub_tab=marey",
      "agencies/:agencyId/analysis/:lens",
    );
    expect(loc.pathname).toBe("/agencies/9/analysis/where");
    expect(loc.search).toBe("?routes=50&sub_tab=marey");
  });

  it("sends network to the compare lens in agencies mode, keeping the dates once", () => {
    const loc = go(
      "agencies/:agencyId/network",
      <RedirectToLens lens="compare" extra={{ mode: "agencies" }} />,
      "/agencies/9/network?from=2026-08-20&to=2026-09-18",
      "agencies/:agencyId/analysis/:lens",
    );
    expect(loc.pathname).toBe("/agencies/9/analysis/compare");
    expect(new URLSearchParams(loc.search).getAll("from")).toEqual(["2026-08-20"]);
    expect(new URLSearchParams(loc.search).get("mode")).toBe("agencies");
  });

  it("sends reports (either view) to saved, keeping the view", () => {
    const loc = go("agencies/:agencyId/reports", <RedirectReportsToSaved />, "/agencies/9/reports?view=saved", "agencies/:agencyId/saved");
    expect(loc.pathname).toBe("/agencies/9/saved");
    expect(loc.search).toBe("?view=saved");
  });

  it("sends reports/:reportType to the lens that hosts it", () => {
    const loc = go(
      "agencies/:agencyId/reports/:reportType",
      <RedirectReportTypeToLens />,
      "/agencies/9/reports/dwell_run?routes=50",
      "agencies/:agencyId/analysis/:lens",
    );
    expect(loc.pathname).toBe("/agencies/9/analysis/why");
    expect(new URLSearchParams(loc.search).get("report")).toBe("dwell_run");
    expect(new URLSearchParams(loc.search).get("routes")).toBe("50");
  });

  it("sends forecast to the when lens's route forecast", () => {
    const loc = go("agencies/:agencyId/forecast", <RedirectForecastToWhen />, "/agencies/9/forecast?routes=50", "agencies/:agencyId/analysis/:lens");
    expect(loc.pathname).toBe("/agencies/9/analysis/when");
    expect(new URLSearchParams(loc.search).get("report")).toBe("route_forecast");
  });
});
