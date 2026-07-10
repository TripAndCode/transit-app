import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";
import { createMemoryRouter, RouterProvider } from "react-router-dom";
import { RedirectReportsToAnalysis, RedirectForecastToAnalysis } from "./legacyRedirects";

function DummyTarget({ label }: { label: string }) {
  return <div>{label}</div>;
}

describe("legacy redirects", () => {
  it("redirects /agencies/:id/reports to /agencies/:id/analysis, preserving the query string", () => {
    const router = createMemoryRouter(
      [
        { path: "agencies/:agencyId/reports", element: <RedirectReportsToAnalysis /> },
        { path: "agencies/:agencyId/analysis", element: <DummyTarget label="analysis-landing" /> },
      ],
      { initialEntries: ["/agencies/8/reports?from=2026-06-07&to=2026-06-10"] },
    );
    render(<RouterProvider router={router} />);
    expect(screen.getByText("analysis-landing")).toBeInTheDocument();
    expect(router.state.location.pathname).toBe("/agencies/8/analysis");
    expect(router.state.location.search).toBe("?from=2026-06-07&to=2026-06-10");
  });

  it("redirects /agencies/:id/reports/:reportType to /agencies/:id/analysis/:reportType", () => {
    const router = createMemoryRouter(
      [
        { path: "agencies/:agencyId/reports/:reportType", element: <RedirectReportsToAnalysis /> },
        { path: "agencies/:agencyId/analysis/:reportType", element: <DummyTarget label="analysis-detail" /> },
      ],
      { initialEntries: ["/agencies/8/reports/trend?from=2026-06-07&to=2026-06-10"] },
    );
    render(<RouterProvider router={router} />);
    expect(screen.getByText("analysis-detail")).toBeInTheDocument();
    expect(router.state.location.pathname).toBe("/agencies/8/analysis/trend");
    expect(router.state.location.search).toBe("?from=2026-06-07&to=2026-06-10");
  });

  it("redirects /agencies/:id/forecast to /agencies/:id/analysis/route_forecast", () => {
    const router = createMemoryRouter(
      [
        { path: "agencies/:agencyId/forecast", element: <RedirectForecastToAnalysis /> },
        { path: "agencies/:agencyId/analysis/:reportType", element: <DummyTarget label="route-forecast-landing" /> },
      ],
      { initialEntries: ["/agencies/8/forecast?from=2026-06-07&to=2026-06-10"] },
    );
    render(<RouterProvider router={router} />);
    expect(screen.getByText("route-forecast-landing")).toBeInTheDocument();
    expect(router.state.location.pathname).toBe("/agencies/8/analysis/route_forecast");
    expect(router.state.location.search).toBe("?from=2026-06-07&to=2026-06-10");
  });
});
