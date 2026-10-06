import { describe, it, expect, vi, afterEach } from "vitest";
import { screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Routes, Route } from "react-router-dom";
import { renderWithProviders } from "../test/renderWithProviders";
import * as hooks from "../api/hooks";
import { ReportsHomeTab } from "./ReportsHomeTab";
import type { Agency, DefinitionMeta, RankingRow, ReportResponse, TrendDay } from "../api/types";

const DEFINITION: DefinitionMeta = {
  preset: null,
  early_tolerance_sec: null,
  late_tolerance_sec: null,
  exclusion_threshold_sec: 7200,
  measurement_point: "all_stops_all_observations",
  dedup_rule: "latest_observation_per_stop_event",
};

function agencies(): Agency[] {
  return [{ agency_id: 1, agency_name: "Hiroden", feed_url: "x", static_url: null, latest_data_date: "2026-04-07" }];
}

function trendResponse(days: TrendDay[] = []): ReportResponse {
  return { report_type: "trend", rendered_at: "2026-06-01T00:00:00Z", text: "", rows: [{ days, hourly: [], dow_band: { grid: [], worst: null } }], definition: DEFINITION };
}

function rankingResponse(rows: RankingRow[] = []): ReportResponse {
  return { report_type: "ranking", rendered_at: "2026-06-01T00:00:00Z", text: "", rows, definition: DEFINITION };
}

function mockReports(trend: ReportResponse, ranking: ReportResponse) {
  vi.spyOn(hooks, "useAgencies").mockReturnValue({ data: agencies(), isLoading: false } as never);
  vi.spyOn(hooks, "useRoutes").mockReturnValue({ data: [], isLoading: false } as never);
  vi.spyOn(hooks, "useReport").mockImplementation((_id, reportType) => {
    if (reportType === "trend") return { data: trend, isPending: false, error: null, isFetching: false, refetch: vi.fn() } as never;
    return { data: ranking, isPending: false, error: null, isFetching: false, refetch: vi.fn() } as never;
  });
}

function renderTab(path = "/agencies/1/reports") {
  renderWithProviders(
    <MemoryRouter initialEntries={[path]}>
      <Routes>
        <Route path="/agencies/:agencyId/reports" element={<ReportsHomeTab />} />
        <Route path="/agencies/:agencyId/routes" element={<p>routes-page</p>} />
      </Routes>
    </MemoryRouter>,
  );
}

describe("ReportsHomeTab", () => {
  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("states its scope as a sentence, greying what the trend did not use", () => {
    mockReports({ ...trendResponse(), scope_applied: { from: true, to: true, time_band: false } } as ReportResponse, rankingResponse());
    renderTab("/agencies/1/reports?time_band=morning");
    expect(screen.getByRole("region", { name: "What you're viewing" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Morning (05–09)" })).toHaveClass("scope-token--off");
  });

  it("writes its period the way the rest of the page writes dates", () => {
    mockReports(trendResponse(), rankingResponse());
    renderTab("/agencies/1/reports?from=2026-08-12&to=2026-09-10");
    const title = screen.getByRole("heading", { level: 2, name: /Hiroden/ });
    expect(title).toHaveTextContent("Aug 12 – Sep 10, 2026");
    expect(title).not.toHaveTextContent("2026-08-12");
    const definitions = screen.getByText("View filters and definitions").closest("details") as HTMLElement;
    expect(definitions).toHaveTextContent("Aug 12 – Sep 10, 2026");
    expect(definitions).not.toHaveTextContent("2026-08-12");
  });

  it("leaves the page heading to the Reports shell", () => {
    mockReports(trendResponse(), rankingResponse());
    renderTab();
    expect(screen.queryByRole("heading", { level: 1 })).toBeNull();
  });

  it("names the routes table's service column and shows its values and counts readably", () => {
    mockReports(trendResponse(), rankingResponse([["101", "平日", 2, 1, 3, 12345] as unknown as RankingRow])); // i18n-ignore: GTFS service name
    renderTab();
    expect(screen.getByRole("columnheader", { name: "Service" })).toBeInTheDocument();
    expect(screen.getByRole("cell", { name: "Weekday" })).toBeInTheDocument();
    expect(screen.getByRole("cell", { name: "12,345" })).toBeInTheDocument();
  });

  it("shows how the figures are made as its own disclosure, not nested in another", () => {
    mockReports(trendResponse(), rankingResponse());
    renderTab();
    const block = screen.getByTestId("definition-meta");
    expect(block.tagName).toBe("DETAILS");
    expect(block.parentElement?.closest("details")).toBeNull();
  });

  it("shows the empty state for both trend and ranking sections when there are no rows", () => {
    mockReports(trendResponse([]), rankingResponse([]));
    renderTab();
    expect(screen.getAllByText("No observations match these filters")).toHaveLength(2);
  });

  it("shows the saved-analyses view and its local-only note from the doc param", () => {
    mockReports(trendResponse(), rankingResponse());
    renderTab("/agencies/1/reports?doc=saved");
    expect(
      screen.getByText("Filters saved in this browser. Opening them queries the latest available data."),
    ).toBeInTheDocument();
  });

  it("says the routes list leaves out routes observed too few times to trust", () => {
    const ranking = { ...rankingResponse([["101", "平日", 2, 1, 3, 400] as unknown as RankingRow]), reliable_min_samples: 100 };
    mockReports(trendResponse(), ranking);
    renderTab();
    expect(screen.getByText("Routes observed fewer than 100 times in the period are left out.")).toBeInTheDocument();
  });

  it("opens a ranking row in its route's dossier and the detailed reports on Time", () => {
    mockReports(trendResponse(), rankingResponse([["101", "平日", 2, 1, 3, 4] as unknown as RankingRow]));
    renderTab("/agencies/1/reports?from=2026-06-01&to=2026-06-07");
    const open = new URL(screen.getByRole("link", { name: "Open analysis →" }).getAttribute("href")!, "http://x");
    expect(open.pathname).toBe("/agencies/1/routes/101");
    expect(open.searchParams.has("routes")).toBe(false);
    expect(open.searchParams.get("service")).toBe("平日");
    const detailed = new URL(screen.getByRole("link", { name: "Detailed reports →" }).getAttribute("href")!, "http://x");
    expect(detailed.pathname).toBe("/agencies/1/time");
    expect(detailed.searchParams.get("report")).toBe("trend");
    expect(detailed.searchParams.get("from")).toBe("2026-06-01");
  });

  it("points an empty saved view at the route pages, where analyses are saved", () => {
    mockReports(trendResponse(), rankingResponse());
    renderTab("/agencies/1/reports?doc=saved");
    expect(screen.getByText("Save filters on a route's page to see them here")).toBeInTheDocument();
  });

  it("takes an empty saved view straight to the route pages", async () => {
    mockReports(trendResponse(), rankingResponse());
    renderTab("/agencies/1/reports?doc=saved");
    await userEvent.click(screen.getByRole("button", { name: "Go to Routes" }));
    expect(screen.getByText("routes-page")).toBeInTheDocument();
  });

  it.each([
    ["routes=50&from=2026-09-01", "/agencies/1/routes/50", "from=2026-09-01"],
    ["routes=50,51", "/agencies/1/routes", "routes=50%2C51"],
  ])("opens a saved analysis %s on its route's page", (query, pathname, search) => {
    mockReports(trendResponse(), rankingResponse());
    localStorage.setItem(
      "transit.savedAnalyses.v1",
      JSON.stringify([{ id: "a", agencyId: 1, title: "Saved one", query, savedAt: "2026-09-01T00:00:00Z" }]),
    );
    renderTab("/agencies/1/reports?doc=saved");
    const link = new URL(screen.getByRole("link", { name: "Saved one" }).getAttribute("href")!, "http://x");
    expect(link.pathname).toBe(pathname);
    expect(link.searchParams.toString()).toBe(search);
    localStorage.removeItem("transit.savedAnalyses.v1");
  });

  it("leaves switching documents to the Reports strip", () => {
    mockReports(trendResponse(), rankingResponse());
    renderTab();
    expect(screen.queryByRole("button", { name: "Saved analyses" })).toBeNull();
  });
});

function renderRecoveryTab(path: string) {
  vi.spyOn(hooks, "useReport").mockReturnValue({ data: trendResponse(), isPending: false, error: null, refetch: vi.fn() } as never);
  vi.spyOn(hooks, "useAgencies").mockReturnValue({
    data: [{ agency_id: 8, agency_name: "A", feed_url: "", static_url: null, latest_data_date: "2026-05-01" }],
    isPending: false,
  } as never);
  renderWithProviders(
    <MemoryRouter initialEntries={[path]}>
      <Routes>
        <Route path="/agencies/:agencyId/reports" element={<ReportsHomeTab />} />
      </Routes>
    </MemoryRouter>,
  );
}

describe("ReportsHomeTab empty state recoveries", () => {
  it("offers a jump-to-latest-data recovery for an empty trend/ranking report", () => {
    renderRecoveryTab("/agencies/8/reports?from=2020-01-01&to=2020-01-07");
    expect(screen.getAllByRole("button", { name: "Jump to the latest data" }).length).toBeGreaterThan(0);
  });

  it("offers a clear-routes recovery when routes are scoped", () => {
    renderRecoveryTab("/agencies/8/reports?from=2020-01-01&to=2020-01-07&routes=A05");
    expect(screen.getAllByRole("button", { name: "Clear the route filter" }).length).toBeGreaterThan(0);
  });
});
