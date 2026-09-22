import { describe, it, expect, vi, afterEach } from "vitest";
import { screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Routes, Route } from "react-router-dom";
import { renderWithProviders } from "../test/renderWithProviders";
import * as hooks from "../api/hooks";
import { AnalysisTab } from "./AnalysisTab";
import type { DefinitionMeta, ReportMeta, ReportResponse, ReportType } from "../api/types";

const DEFINITION: DefinitionMeta = {
  preset: "legacy_60s",
  early_tolerance_sec: null,
  late_tolerance_sec: 60,
  exclusion_threshold_sec: 7200,
  measurement_point: "all_stops_all_observations",
  dedup_rule: "latest_observation_per_stop_event",
};

function reportMeta(reportType: ReportType): ReportMeta {
  return { report_type: reportType, rendered_at: "2026-06-01T00:00:00Z" };
}

function reportResponse(reportType: ReportType): ReportResponse {
  // `rows: []` is valid for every member of the union, but TypeScript can't
  // pick one from a variable discriminant -- the cast names the shape the
  // caller is standing in for rather than widening `rows` back to unknown[].
  return { report_type: reportType, rendered_at: "2026-06-01T00:00:00Z", text: "", rows: [], definition: DEFINITION } as ReportResponse;
}

function mockSupportHooks() {
  vi.spyOn(hooks, "useRoutes").mockReturnValue({ data: [], isLoading: false } as never);
  vi.spyOn(hooks, "useSuggestion").mockReturnValue({ data: null, isLoading: false } as never);
}

function renderAnalysis(initialPath: string) {
  renderWithProviders(
    <MemoryRouter initialEntries={[initialPath]}>
      <Routes>
        <Route path="/agencies/:agencyId/analysis/:reportType?" element={<AnalysisTab />} />
      </Routes>
    </MemoryRouter>,
  );
}

describe("AnalysisTab", () => {
  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("prompts to select a report when none is chosen yet", () => {
    mockSupportHooks();
    vi.spyOn(hooks, "useReports").mockReturnValue({
      data: [reportMeta("ranking"), reportMeta("on_time")],
      isLoading: false,
      error: null,
      refetch: vi.fn(),
    } as never);
    vi.spyOn(hooks, "useReport").mockReturnValue({ data: undefined, isFetching: false, error: null, refetch: vi.fn() } as never);
    renderAnalysis("/agencies/1/analysis");
    expect(screen.getByText("Reports")).toBeInTheDocument();
    expect(screen.getByText("Select a report")).toBeInTheDocument();
  });

  it("shows the no-data empty state when the selected report has no rows", () => {
    mockSupportHooks();
    vi.spyOn(hooks, "useReports").mockReturnValue({
      data: [reportMeta("ranking")],
      isLoading: false,
      error: null,
      refetch: vi.fn(),
    } as never);
    vi.spyOn(hooks, "useReport").mockReturnValue({
      data: reportResponse("ranking"),
      isFetching: false,
      error: null,
      refetch: vi.fn(),
    } as never);
    renderAnalysis("/agencies/1/analysis/ranking");
    expect(screen.getByText("No matching data")).toBeInTheDocument();
  });

  it("marks the clicked report as active in the URL-driven list, switching report-type selection", async () => {
    mockSupportHooks();
    vi.spyOn(hooks, "useReports").mockReturnValue({
      data: [reportMeta("ranking"), reportMeta("on_time")],
      isLoading: false,
      error: null,
      refetch: vi.fn(),
    } as never);
    vi.spyOn(hooks, "useReport").mockReturnValue({ data: undefined, isFetching: false, error: null, refetch: vi.fn() } as never);
    renderAnalysis("/agencies/1/analysis");

    // The grouped list puts the report's one-line description inside the
    // same button, so its accessible name is the label plus that sentence.
    const onTimeButton = screen.getByRole("button", { name: /^On-time rate/ });
    expect(onTimeButton).toHaveAttribute("aria-pressed", "false");

    await userEvent.click(onTimeButton);

    expect(onTimeButton).toHaveAttribute("aria-pressed", "true");
    expect(screen.getByRole("button", { name: /^Delay ranking/ })).toHaveAttribute("aria-pressed", "false");
  });
});

function emptyReport(reportType: string): ReportResponse {
  return {
    report_type: reportType,
    rendered_at: "2026-09-01T00:00:00Z",
    text: "",
    rows: [],
    definition: {
      preset: null,
      early_tolerance_sec: null,
      late_tolerance_sec: null,
      exclusion_threshold_sec: 0,
      measurement_point: "departure",
      dedup_rule: "",
    },
  } as ReportResponse;
}

function renderRecoveryTab(path: string, reportType = "ranking") {
  vi.spyOn(hooks, "useReports").mockReturnValue({ data: [], isLoading: false, error: null } as never);
  vi.spyOn(hooks, "useReport").mockReturnValue({
    data: emptyReport(reportType),
    isPending: false,
    error: null,
    refetch: vi.fn(),
  } as never);
  vi.spyOn(hooks, "useAgencies").mockReturnValue({
    data: [{ agency_id: 8, agency_name: "A", feed_url: "", static_url: null, latest_data_date: "2026-05-01" }],
    isPending: false,
  } as never);
  vi.spyOn(hooks, "useRoutes").mockReturnValue({ data: [], isLoading: false } as never);
  renderWithProviders(
    <MemoryRouter initialEntries={[path]}>
      <Routes>
        <Route path="/agencies/:agencyId/analysis/:reportType" element={<AnalysisTab />} />
      </Routes>
    </MemoryRouter>,
  );
}

describe("AnalysisTab empty state recoveries", () => {
  it("offers a jump-to-latest-data recovery for an empty ranking report", () => {
    renderRecoveryTab("/agencies/8/analysis/ranking?from=2020-01-01&to=2020-01-07");
    expect(screen.getByRole("button", { name: "Jump to the latest data" })).toBeInTheDocument();
  });

  it("offers a clear-routes recovery when routes are scoped", () => {
    renderRecoveryTab("/agencies/8/analysis/ranking?from=2020-01-01&to=2020-01-07&routes=A05");
    expect(screen.getByRole("button", { name: "Clear the route filter" })).toBeInTheDocument();
  });

  it("offers the dwell_run recovery only for the no-routes case, not the not_available/unsupported states", () => {
    vi.spyOn(hooks, "useReports").mockReturnValue({ data: [], isLoading: false, error: null } as never);
    vi.spyOn(hooks, "useAgencies").mockReturnValue({
      data: [{ agency_id: 8, agency_name: "A", feed_url: "", static_url: null, latest_data_date: "2026-05-01" }],
      isPending: false,
    } as never);
    vi.spyOn(hooks, "useRoutes").mockReturnValue({ data: [], isLoading: false } as never);
    vi.spyOn(hooks, "useReport").mockReturnValue({
      data: { ...emptyReport("dwell_run"), rows: [{ available: true, time_band_supported: true, routes: [] }] },
      isPending: false,
      error: null,
      refetch: vi.fn(),
    } as never);
    renderWithProviders(
      <MemoryRouter initialEntries={["/agencies/8/analysis/dwell_run?from=2020-01-01&to=2020-01-07&routes=A05"]}>
        <Routes>
          <Route path="/agencies/:agencyId/analysis/:reportType" element={<AnalysisTab />} />
        </Routes>
      </MemoryRouter>,
    );
    expect(screen.getByRole("button", { name: "Clear the route filter" })).toBeInTheDocument();
  });
});
