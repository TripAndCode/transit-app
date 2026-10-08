import { describe, expect, it, vi } from "vitest";
import { screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { createMemoryRouter, RouterProvider } from "react-router-dom";
import { renderWithProviders } from "../test/renderWithProviders";
import { CompareTab } from "./CompareTab";
import { TimeTab } from "./TimeTab";
import { WhyTab } from "./WhyTab";

vi.mock("../routes/lazyTabs", () => ({
  // NetworkTab titles itself; the stand-in keeps that heading so the count
  // of level-1 headings on Compare by agencies is the real one.
  loadNetworkTab: () =>
    Promise.resolve({
      default: () => (
        <div>
          <h1>Compare agencies</h1>network-tab
        </div>
      ),
    }),
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

  it.each([
    ["/agencies/9/time", "agencies/:agencyId/time", <TimeTab key="time" />, "Time"],
    ["/agencies/9/why", "agencies/:agencyId/why", <WhyTab key="why" />, "Why"],
    ["/agencies/9/compare", "agencies/:agencyId/compare", <CompareTab key="compare" />, "Compare"],
  ] as const)("titles %s with one level-1 heading", async (path, route, element, title) => {
    open(path, route, element);
    await screen.findByText(/^analysis-tab/);
    expect(screen.getByRole("heading", { level: 1, name: title })).toBeInTheDocument();
  });

  it.each([
    ["?by=periods", /^analysis-tab/, "Compare"],
    ["?by=agencies", /network-tab/, "Compare agencies"],
  ])("has exactly one level-1 heading on Compare%s", async (search, content, title) => {
    open(`/agencies/9/compare${search}`, "agencies/:agencyId/compare", <CompareTab />);
    await screen.findByText(content);
    const headings = screen.getAllByRole("heading", { level: 1 });
    expect(headings).toHaveLength(1);
    expect(headings[0]).toHaveTextContent(title);
  });

  it("keeps the Compare title above the mode toggle", async () => {
    open("/agencies/9/compare", "agencies/:agencyId/compare", <CompareTab />);
    await screen.findByText(/^analysis-tab/);
    const heading = screen.getByRole("heading", { level: 1, name: "Compare" });
    const toggle = screen.getByRole("group", { name: "Compare" });
    expect(heading.compareDocumentPosition(toggle) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
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
