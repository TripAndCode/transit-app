import { describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import { createMemoryRouter, RouterProvider } from "react-router-dom";
import { I18nextProvider } from "react-i18next";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import i18n from "../i18n";

vi.mock("../routes/lazyTabs", () => ({
  loadOverviewTab: () => Promise.resolve({ default: () => <div>overview-tab</div> }),
  loadRouteAnalysisTab: () => Promise.resolve({ default: () => <div>route-analysis-tab</div> }),
  loadNetworkTab: () => Promise.resolve({ default: () => <div>network-tab</div> }),
  loadAnalysisTab: () =>
    Promise.resolve({
      default: ({ reportTypes }: { reportTypes: readonly string[] }) => <div>analysis-tab:{reportTypes.join(",")}</div>,
    }),
}));

import { AnalysisWorkspace } from "./AnalysisWorkspace";

void i18n.changeLanguage("en");

function open(path: string) {
  const router = createMemoryRouter([{ path: "agencies/:agencyId/analysis/:lens", element: <AnalysisWorkspace /> }], {
    initialEntries: [path],
  });
  render(
    <QueryClientProvider client={new QueryClient()}>
      <I18nextProvider i18n={i18n}>
        <RouterProvider router={router} />
      </I18nextProvider>
    </QueryClientProvider>,
  );
  return router;
}

describe("AnalysisWorkspace", () => {
  it("renders the overview lens with the existing overview tab", async () => {
    open("/agencies/9/analysis/overview");
    expect(await screen.findByText("overview-tab")).toBeInTheDocument();
  });

  it("hosts the when lens's report types in AnalysisTab", async () => {
    open("/agencies/9/analysis/when");
    expect(await screen.findByText("analysis-tab:trend,dow_weekday,dow_weekend,route_forecast")).toBeInTheDocument();
  });

  it("renders route analysis on the where lens", async () => {
    open("/agencies/9/analysis/where?routes=50");
    expect(await screen.findByText("route-analysis-tab")).toBeInTheDocument();
  });

  it("switches the compare lens to the network board in agencies mode", async () => {
    open("/agencies/9/analysis/compare?mode=agencies");
    expect(await screen.findByText("network-tab")).toBeInTheDocument();
  });

  it("redirects an old report-type segment to the lens hosting it", async () => {
    const router = open("/agencies/9/analysis/dwell_run?routes=50");
    expect(await screen.findByText("analysis-tab:dwell_run")).toBeInTheDocument();
    expect(router.state.location.pathname).toBe("/agencies/9/analysis/why");
    expect(new URLSearchParams(router.state.location.search).get("report")).toBe("dwell_run");
  });

  it("sends an unknown segment to overview", async () => {
    const router = open("/agencies/9/analysis/banana");
    expect(await screen.findByText("overview-tab")).toBeInTheDocument();
    expect(router.state.location.pathname).toBe("/agencies/9/analysis/overview");
  });
});
