import { describe, it, expect, vi, afterEach } from "vitest";
import { screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Routes, Route } from "react-router-dom";
import { renderWithProviders } from "../test/renderWithProviders";
import * as hooks from "../api/hooks";
import { ReportsHomeTab } from "./ReportsHomeTab";
import type { Agency, DefinitionMeta, ReportResponse } from "../api/types";

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

function trendResponse(days: unknown[] = []): ReportResponse {
  return { report_type: "trend", rendered_at: "2026-06-01T00:00:00Z", text: "", rows: [{ days }], definition: DEFINITION };
}

function rankingResponse(rows: unknown[][] = []): ReportResponse {
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

function renderTab() {
  renderWithProviders(
    <MemoryRouter initialEntries={["/agencies/1/reports"]}>
      <Routes>
        <Route path="/agencies/:agencyId/reports" element={<ReportsHomeTab />} />
      </Routes>
    </MemoryRouter>,
  );
}

describe("ReportsHomeTab", () => {
  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("renders the report heading and description", () => {
    mockReports(trendResponse(), rankingResponse());
    renderTab();
    expect(screen.getByRole("heading", { name: "Reports" })).toBeInTheDocument();
    expect(screen.getByText("Summarize service performance in one page")).toBeInTheDocument();
  });

  it("shows the empty state for both trend and ranking sections when there are no rows", () => {
    mockReports(trendResponse([]), rankingResponse([]));
    renderTab();
    expect(screen.getAllByText("No observations match these filters")).toHaveLength(2);
  });

  it("switches to the saved-analyses view and shows its local-only note", async () => {
    mockReports(trendResponse(), rankingResponse());
    renderTab();
    await userEvent.click(screen.getByRole("button", { name: "Saved analyses" }));
    expect(
      screen.getByText("Filters saved in this browser. Opening them queries the latest available data."),
    ).toBeInTheDocument();
  });
});
