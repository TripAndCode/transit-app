import { describe, it, expect, vi, afterEach } from "vitest";
import { screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Routes, Route } from "react-router-dom";
import { renderWithProviders } from "../test/renderWithProviders";
import * as hooks from "../api/hooks";
import { AnalysisTab } from "./AnalysisTab";
import type { DefinitionMeta, ReportMeta, ReportResponse } from "../api/types";

const DEFINITION: DefinitionMeta = {
  preset: "legacy_60s",
  early_tolerance_sec: null,
  late_tolerance_sec: 60,
  exclusion_threshold_sec: 7200,
  measurement_point: "all_stops_all_observations",
  dedup_rule: "latest_observation_per_stop_event",
};

function reportMeta(reportType: string): ReportMeta {
  return { report_type: reportType, rendered_at: "2026-06-01T00:00:00Z" };
}

function reportResponse(reportType: string): ReportResponse {
  return { report_type: reportType, rendered_at: "2026-06-01T00:00:00Z", text: "", rows: [], definition: DEFINITION };
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

    const onTimeButton = screen.getByRole("button", { name: "On-time rate" });
    expect(onTimeButton).toHaveAttribute("aria-pressed", "false");

    await userEvent.click(onTimeButton);

    expect(onTimeButton).toHaveAttribute("aria-pressed", "true");
    expect(screen.getByRole("button", { name: "Delay ranking" })).toHaveAttribute("aria-pressed", "false");
  });
});
