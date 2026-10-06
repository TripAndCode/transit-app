import { describe, expect, it, vi } from "vitest";
import { screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { createMemoryRouter, RouterProvider } from "react-router-dom";
import { renderWithProviders } from "../test/renderWithProviders";
import { CompareTab } from "./CompareTab";
import { TimeTab } from "./TimeTab";
import { WhyTab } from "./WhyTab";

vi.mock("../routes/lazyTabs", () => ({
  loadNetworkTab: () => Promise.resolve({ default: () => <div>network-tab</div> }),
  loadAnalysisTab: () =>
    Promise.resolve({
      default: ({ reportTypes }: { reportTypes: readonly string[] }) => <div>analysis-tab:{reportTypes.join(",")}</div>,
    }),
}));

function open(path: string, route: string, element: React.ReactNode) {
  const router = createMemoryRouter([{ path: route, element }], { initialEntries: [path] });
  renderWithProviders(<RouterProvider router={router} />);
  return router;
}

describe("destination screens", () => {
  it("hosts the time report types on Time", async () => {
    open("/agencies/9/time", "agencies/:agencyId/time", <TimeTab />);
    expect(await screen.findByText("analysis-tab:trend,dow_weekday,dow_weekend,route_forecast")).toBeInTheDocument();
  });

  it("hosts dwell vs run on Why", async () => {
    open("/agencies/9/why", "agencies/:agencyId/why", <WhyTab />);
    expect(await screen.findByText("analysis-tab:dwell_run")).toBeInTheDocument();
  });

  it("shows the agencies board on Compare by agencies and switches back to periods", async () => {
    const router = open("/agencies/9/compare?by=agencies&from=2026-09-01", "agencies/:agencyId/compare", <CompareTab />);
    expect(await screen.findByText("network-tab")).toBeInTheDocument();
    const agencies = screen.getByRole("button", { name: "Agencies" });
    const periods = screen.getByRole("button", { name: "Weekdays and weekends" });
    expect(agencies).toHaveAttribute("aria-pressed", "true");
    expect(periods).toHaveAttribute("aria-pressed", "false");
    await userEvent.click(periods);
    expect(await screen.findByText("analysis-tab:compare_ranking")).toBeInTheDocument();
    const search = new URLSearchParams(router.state.location.search);
    expect(search.get("by")).toBe("periods");
    expect(search.get("from")).toBe("2026-09-01");
  });

  it.each(["", "?by=service", "?by=periods"])("shows the period comparison on Compare%s", async (search) => {
    open(`/agencies/9/compare${search}`, "agencies/:agencyId/compare", <CompareTab />);
    expect(await screen.findByText("analysis-tab:compare_ranking")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Weekdays and weekends" })).toHaveAttribute("aria-pressed", "true");
  });
});
