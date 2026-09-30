import { describe, it, expect, vi, afterEach } from "vitest";
import { screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { createMemoryRouter, MemoryRouter, RouterProvider, Routes, Route, useNavigate } from "react-router-dom";
import { renderWithProviders } from "../test/renderWithProviders";
import * as hooks from "../api/hooks";
import { AnalysisTab } from "./AnalysisTab";
import type { DefinitionMeta, ReportMeta, ReportResponse, ReportType } from "../api/types";

vi.mock("../components/HeadwayQualityPanel", () => ({ HeadwayQualityPanel: () => <div>headway-panel</div> }));
vi.mock("../components/PerformanceStandardPanel", () => ({ PerformanceStandardPanel: () => <div>standards-panel</div> }));
vi.mock("../components/WeatherDelayPanel", () => ({ WeatherDelayPanel: () => <div>weather-panel</div> }));

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

function renderAnalysis(
  initialPath: string,
  reportTypes: readonly string[] = ["ranking", "on_time"],
  defaultReport?: string,
) {
  const router = createMemoryRouter(
    [
      {
        path: "/agencies/:agencyId/analysis/:lens",
        element: <AnalysisTab reportTypes={reportTypes} defaultReport={defaultReport} />,
      },
    ],
    { initialEntries: [initialPath] },
  );
  renderWithProviders(<RouterProvider router={router} />);
  return { router };
}

const WHEN_TYPES = ["trend", "dow_weekday", "dow_weekend", "route_forecast"] as const;

describe("AnalysisTab", () => {
  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("opens its first report when no report is chosen", () => {
    mockSupportHooks();
    vi.spyOn(hooks, "useReports").mockReturnValue({
      data: [reportMeta("ranking"), reportMeta("on_time")],
      isLoading: false,
      error: null,
      refetch: vi.fn(),
    } as never);
    const useReport = vi.spyOn(hooks, "useReport").mockReturnValue({ data: undefined, isFetching: false, error: null, refetch: vi.fn() } as never);
    renderAnalysis("/agencies/1/analysis/rider");
    expect(screen.getByText("Reports")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /^Delay ranking/ })).toHaveAttribute("aria-pressed", "true");
    expect(useReport.mock.calls.at(-1)?.[1]).toBe("ranking");
  });

  it.each([
    ["on_time", "on_time"],
    ["trend", "ranking"],
  ])("opens default report %s as %s when no report is chosen", (defaultReport, opened) => {
    mockSupportHooks();
    vi.spyOn(hooks, "useReports").mockReturnValue({
      data: [reportMeta("ranking"), reportMeta("on_time")],
      isLoading: false,
      error: null,
      refetch: vi.fn(),
    } as never);
    const useReport = vi.spyOn(hooks, "useReport").mockReturnValue({ data: undefined, isFetching: false, error: null, refetch: vi.fn() } as never);
    renderAnalysis("/agencies/1/analysis/rider", ["ranking", "on_time"], defaultReport);
    expect(useReport.mock.calls.at(-1)?.[1]).toBe(opened);
  });

  it("lists only the report types it is given", () => {
    mockSupportHooks();
    vi.spyOn(hooks, "useReports").mockReturnValue({
      data: [reportMeta("ranking"), reportMeta("trend"), reportMeta("dow_weekday"), reportMeta("dwell_run")],
      isLoading: false,
      error: null,
      refetch: vi.fn(),
    } as never);
    vi.spyOn(hooks, "useReport").mockReturnValue({ data: undefined, isFetching: false, error: null, refetch: vi.fn() } as never);
    renderAnalysis("/agencies/1/analysis/when", WHEN_TYPES);
    expect(screen.getByRole("button", { name: /^Trend/ })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /^Route forecast/ })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /^Dwell\/running time/ })).toBeNull();
    expect(screen.queryByRole("button", { name: /^Delay ranking/ })).toBeNull();
  });

  it("falls back to its first report when the report param belongs to another screen", () => {
    mockSupportHooks();
    vi.spyOn(hooks, "useReports").mockReturnValue({
      data: [reportMeta("trend"), reportMeta("dwell_run")],
      isLoading: false,
      error: null,
      refetch: vi.fn(),
    } as never);
    const useReport = vi.spyOn(hooks, "useReport").mockReturnValue({ data: undefined, isFetching: false, error: null, refetch: vi.fn() } as never);
    renderAnalysis("/agencies/1/analysis/why?report=trend", ["dwell_run"]);
    expect(screen.getByRole("button", { name: /^Dwell\/running time/ })).toHaveAttribute("aria-pressed", "true");
    expect(useReport.mock.calls.at(-1)?.[1]).toBe("dwell_run");
  });

  it("selecting a report sets the report param, keeps the filters, and adds a history entry", async () => {
    mockSupportHooks();
    vi.spyOn(hooks, "useReports").mockReturnValue({
      data: [reportMeta("trend"), reportMeta("dow_weekday"), reportMeta("dow_weekend")],
      isLoading: false,
      error: null,
      refetch: vi.fn(),
    } as never);
    vi.spyOn(hooks, "useReport").mockReturnValue({ data: undefined, isFetching: false, error: null, refetch: vi.fn() } as never);
    const { router } = renderAnalysis("/agencies/1/analysis/when?routes=50", WHEN_TYPES);
    await userEvent.click(screen.getByRole("button", { name: /^Weekday pattern/ }));
    const params = new URLSearchParams(router.state.location.search);
    expect(params.get("report")).toBe("dow_weekday");
    expect(params.get("routes")).toBe("50");
    expect(router.state.historyAction).toBe("PUSH");
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
    renderAnalysis("/agencies/1/analysis/rider?report=ranking");
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
    renderAnalysis("/agencies/1/analysis/rider");

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

function renderRecoveryTab(path: string, reportType = "ranking", reportTypes: readonly string[] = ["ranking"]) {
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
        <Route path="/agencies/:agencyId/analysis/:lens" element={<AnalysisTab reportTypes={reportTypes} />} />
      </Routes>
    </MemoryRouter>,
  );
}

describe("AnalysisTab empty state recoveries", () => {
  it("offers a jump-to-latest-data recovery for an empty ranking report", () => {
    renderRecoveryTab("/agencies/8/analysis/rider?report=ranking&from=2020-01-01&to=2020-01-07");
    expect(screen.getByRole("button", { name: "Jump to the latest data" })).toBeInTheDocument();
  });

  it("offers a clear-routes recovery when routes are scoped", () => {
    renderRecoveryTab("/agencies/8/analysis/rider?report=ranking&from=2020-01-01&to=2020-01-07&routes=A05");
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
      <MemoryRouter initialEntries={["/agencies/8/analysis/why?report=dwell_run&from=2020-01-01&to=2020-01-07&routes=A05"]}>
        <Routes>
          <Route path="/agencies/:agencyId/analysis/:lens" element={<AnalysisTab reportTypes={["dwell_run"]} />} />
        </Routes>
      </MemoryRouter>,
    );
    expect(screen.getByRole("button", { name: "Clear the route filter" })).toBeInTheDocument();
  });
});

describe("AnalysisTab dwell_run route cap", () => {
  afterEach(() => {
    vi.restoreAllMocks();
  });

  function SwitchAgency() {
    const navigate = useNavigate();
    return (
      <button type="button" onClick={() => navigate("/agencies/9/analysis/why?report=dwell_run&from=2020-01-01&to=2020-01-07")}>
        switch agency
      </button>
    );
  }

  it("keeps a raised cap across a refetch of the same report and re-caps when the agency changes", async () => {
    const user = userEvent.setup();
    // A fresh payload per call: every refetch hands back new objects even
    // when nothing in them changed.
    const dwellRun = () => ({
      data: {
        ...emptyReport("dwell_run"),
        rows: [
          {
            available: true,
            time_band_supported: true,
            routes: Array.from({ length: 250 }, (_, i) => ({
              route_code: `R${i + 1}`,
              service_type: null,
              dwell_samples: 1,
              dwell_avg_sec: null,
              dwell_p50_sec: null,
              dwell_p90_sec: null,
              run_samples: 1,
              run_avg_sec: null,
              run_p50_sec: null,
              run_p90_sec: null,
            })),
          },
        ],
      },
      isPending: false,
      error: null,
      refetch: vi.fn(),
    });
    let current = dwellRun();
    vi.spyOn(hooks, "useReports").mockReturnValue({ data: [], isLoading: false, error: null } as never);
    vi.spyOn(hooks, "useReport").mockImplementation(() => current as never);
    vi.spyOn(hooks, "useAgencies").mockReturnValue({ data: [], isPending: false } as never);
    vi.spyOn(hooks, "useRoutes").mockReturnValue({ data: [], isLoading: false } as never);
    const ui = () => (
      <MemoryRouter initialEntries={["/agencies/8/analysis/why?report=dwell_run&from=2020-01-01&to=2020-01-07"]}>
        <SwitchAgency />
        <Routes>
          <Route path="/agencies/:agencyId/analysis/:lens" element={<AnalysisTab reportTypes={["dwell_run"]} />} />
        </Routes>
      </MemoryRouter>
    );
    const routeCells = () => screen.getAllByText(/^Route R\d+$/);

    const { rerender } = renderWithProviders(ui());
    expect(routeCells()).toHaveLength(200);
    await user.click(screen.getByRole("button", { name: "Show 50 more" }));
    expect(routeCells()).toHaveLength(250);

    current = dwellRun();
    rerender(ui());
    expect(routeCells()).toHaveLength(250);

    await user.click(screen.getByRole("button", { name: "switch agency" }));
    expect(routeCells()).toHaveLength(200);
  });
});

describe("AnalysisTab evidence panels", () => {
  afterEach(() => {
    vi.restoreAllMocks();
  });

  function show(path: string, reportType: ReportType, rows: unknown[]) {
    mockSupportHooks();
    vi.spyOn(hooks, "useReports").mockReturnValue({ data: [reportMeta(reportType)], isLoading: false, error: null, refetch: vi.fn() } as never);
    vi.spyOn(hooks, "useReport").mockReturnValue({
      data: { ...reportResponse(reportType), rows },
      isFetching: false,
      error: null,
      refetch: vi.fn(),
    } as never);
    renderAnalysis(path, [reportType]);
  }

  it("puts the rain and long-gap panels beside dwell vs run in Why", () => {
    show("/agencies/1/analysis/why", "dwell_run", [{ available: true, time_band_supported: true, routes: [] }]);
    expect(screen.getByText("weather-panel")).toBeInTheDocument();
    expect(screen.getByText("headway-panel")).toBeInTheDocument();
    expect(screen.queryByText("standards-panel")).toBeNull();
  });

  it("puts the headway and targets panels beside on-time, without rain", () => {
    show("/agencies/1/analysis/rider", "on_time", []);
    expect(screen.getByText("headway-panel")).toBeInTheDocument();
    expect(screen.getByText("standards-panel")).toBeInTheDocument();
    expect(screen.queryByText("weather-panel")).toBeNull();
  });
});
