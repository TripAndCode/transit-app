import { describe, expect, it, vi } from "vitest";
import { screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { createMemoryRouter, RouterProvider } from "react-router-dom";
import { renderWithProviders } from "../test/renderWithProviders";
import { CompareTab } from "./CompareTab";
import { TimeTab } from "./TimeTab";
import { WhyTab } from "./WhyTab";

vi.mock("../routes/lazyTabs", () => ({
  // NetworkTab titles its board with a level-2 heading (NetworkTab.test
  // pins that); the stand-in keeps it so the heading counts here are real.
  loadNetworkTab: () =>
    Promise.resolve({
      default: () => (
        <div>
          <h2>Compare agencies</h2>network-tab
        </div>
      ),
    }),
  loadSupplyMapView: () => Promise.resolve({ default: () => <div>supply-map</div> }),
  loadAnalysisTab: () =>
    Promise.resolve({
      default: ({ reportTypes }: { reportTypes: readonly string[] }) => <div>analysis-tab:{reportTypes.join(",")}</div>,
    }),
  loadSingleReportScreen: () =>
    Promise.resolve({
      default: ({ reportType }: { reportType: string }) => <div>single-report:{reportType}</div>,
    }),
  loadSlowZonesView: () => Promise.resolve({ default: () => <div>slow-zones-view</div> }),
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
    expect(await screen.findByText("single-report:dwell_run")).toBeInTheDocument();
  });

  it("shows the slow segments on Why by segments and switches back to dwell vs run", async () => {
    const router = open("/agencies/9/why?by=segments", "agencies/:agencyId/why", <WhyTab />);
    expect(await screen.findByText("slow-zones-view")).toBeInTheDocument();
    const segments = screen.getByRole("button", { name: "Slow segments" });
    const dwell = screen.getByRole("button", { name: "Dwell/running time" });
    expect(segments).toHaveAttribute("aria-pressed", "true");
    await userEvent.click(dwell);
    expect(await screen.findByText("single-report:dwell_run")).toBeInTheDocument();
    expect(new URLSearchParams(router.state.location.search).get("by")).toBe("dwell_run");
    expect(screen.getAllByRole("heading", { level: 1 })).toHaveLength(1);
  });

  it.each([
    ["/agencies/9/time", "agencies/:agencyId/time", <TimeTab key="time" />, "Time"],
    ["/agencies/9/why", "agencies/:agencyId/why", <WhyTab key="why" />, "Why"],
    ["/agencies/9/compare", "agencies/:agencyId/compare", <CompareTab key="compare" />, "Day types & agencies"],
  ] as const)("titles %s with one level-1 heading", async (path, route, element, title) => {
    open(path, route, element);
    await screen.findByText(/^(analysis-tab|single-report)/);
    expect(screen.getByRole("heading", { level: 1, name: title })).toBeInTheDocument();
  });

  it.each([
    ["?by=periods", /^single-report/, "Day types & agencies"],
    ["?by=agencies", /network-tab/, "Day types & agencies"],
  ])("has exactly one level-1 heading on Compare%s", async (search, content, title) => {
    open(`/agencies/9/compare${search}`, "agencies/:agencyId/compare", <CompareTab />);
    await screen.findByText(content);
    const headings = screen.getAllByRole("heading", { level: 1 });
    expect(headings).toHaveLength(1);
    expect(headings[0]).toHaveTextContent(title);
  });

  it.each([
    ["?by=periods", /^single-report/],
    ["?by=agencies", /network-tab/],
  ])("keeps the same title directly above the mode toggle on Compare%s, so switching modes does not move it", async (search, content) => {
    open(`/agencies/9/compare${search}`, "agencies/:agencyId/compare", <CompareTab />);
    await screen.findByText(content);
    const heading = screen.getByRole("heading", { level: 1, name: "Day types & agencies" });
    const toggle = screen.getByRole("group", { name: "Day types & agencies" });
    expect(heading.closest("header")?.nextElementSibling).toBe(toggle);
  });

  it("shows the agencies board on Compare by agencies and switches back to periods", async () => {
    const router = open("/agencies/9/compare?by=agencies&from=2026-09-01", "agencies/:agencyId/compare", <CompareTab />);
    expect(await screen.findByText("network-tab")).toBeInTheDocument();
    const agencies = screen.getByRole("button", { name: "Agencies" });
    const periods = screen.getByRole("button", { name: "Weekdays and weekends/holidays" });
    expect(agencies).toHaveAttribute("aria-pressed", "true");
    expect(periods).toHaveAttribute("aria-pressed", "false");
    await userEvent.click(periods);
    expect(await screen.findByText("single-report:compare_ranking")).toBeInTheDocument();
    const search = new URLSearchParams(router.state.location.search);
    expect(search.get("by")).toBe("periods");
    expect(search.get("from")).toBe("2026-09-01");
  });

  it("shows the planned departures map on Compare by supply", async () => {
    open("/agencies/9/compare?by=supply", "agencies/:agencyId/compare", <CompareTab />);
    expect(await screen.findByText("supply-map")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Planned departures" })).toHaveAttribute("aria-pressed", "true");
    expect(screen.getAllByRole("heading", { level: 1 })).toHaveLength(1);
  });

  it.each(["", "?by=service", "?by=periods"])("shows the period comparison on Compare%s", async (search) => {
    open(`/agencies/9/compare${search}`, "agencies/:agencyId/compare", <CompareTab />);
    expect(await screen.findByText("single-report:compare_ranking")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Weekdays and weekends/holidays" })).toHaveAttribute("aria-pressed", "true");
  });
});
