import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { describe, it, expect, vi, afterEach, beforeEach } from "vitest";
import { act, cleanup, fireEvent, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { createMemoryRouter, MemoryRouter, RouterProvider, Routes, Route, useNavigate } from "react-router-dom";
import { renderWithProviders } from "../test/renderWithProviders";
import { decl, ruleBody } from "../test/cssRules";
import * as hooks from "../api/hooks";
import * as adminHook from "../api/useIsAdmin";
import { AnalysisTab } from "./AnalysisTab";
import { STILL_WORKING_AFTER_MS } from "../components/StillWorking";
import type { DefinitionMeta, ReportMeta, ReportResponse, ReportType, TrendPayload } from "../api/types";

vi.mock("../components/HeadwayQualityPanel", () => ({ HeadwayQualityPanel: () => <div>headway-panel</div> }));
vi.mock("../components/PerformanceStandardPanel", () => ({ PerformanceStandardPanel: () => <div>standards-panel</div> }));
vi.mock("../components/WeatherDelayPanel", () => ({ WeatherDelayPanel: () => <div>weather-panel</div> }));

// Why reads the rain and headway answers itself to sort what it can show from
// what it can't. Still loading, both keep their panels (mocked above).
beforeEach(() => {
  const loading = { data: undefined, isLoading: true, error: null, refetch: vi.fn() } as never;
  vi.spyOn(hooks, "useWeatherDelay").mockReturnValue(loading);
  vi.spyOn(hooks, "useHeadwayQuality").mockReturnValue(loading);
});

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
    vi.unstubAllEnvs();
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

  it("greys the scope conditions the open report did not use", () => {
    mockSupportHooks();
    vi.spyOn(hooks, "useReports").mockReturnValue({ data: [reportMeta("dow_weekday")], isLoading: false, error: null, refetch: vi.fn() } as never);
    vi.spyOn(hooks, "useReport").mockReturnValue({
      data: { ...reportResponse("dow_weekday"), scope_applied: { from: true, to: true, dow: false, service: false } },
      isFetching: false,
      error: null,
      refetch: vi.fn(),
    } as never);
    renderAnalysis("/agencies/1/analysis/when?report=dow_weekday&dow=weekday&routes=R1", ["dow_weekday"]);
    expect(screen.getByRole("region", { name: "What you're viewing" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "weekdays" })).toHaveClass("scope-token--off");
    expect(screen.getByRole("button", { name: /R1/ })).not.toHaveClass("scope-token--off");
  });

  it("greys by the forecast's own scope on the route forecast, not the last report's", () => {
    mockSupportHooks();
    vi.spyOn(hooks, "useReports").mockReturnValue({ data: [reportMeta("dow_weekday")], isLoading: false, error: null, refetch: vi.fn() } as never);
    vi.spyOn(hooks, "useReport").mockReturnValue({
      data: { ...reportResponse("dow_weekday"), scope_applied: { from: true, to: true, dow: false, time_band: true } },
      isFetching: false,
      error: null,
      refetch: vi.fn(),
    } as never);
    vi.spyOn(hooks, "useForecastOverview").mockReturnValue({
      data: {
        grid: [],
        worst: null,
        routes: [],
        forecast_days: [],
        history_from: null,
        history_to: null,
        recent_from: null,
        recent_to: null,
        disclaimer: "",
        scope_applied: { from: false, to: false, dow: false, time_band: false, routes: false },
      },
      isPending: false,
      error: null,
      refetch: vi.fn(),
    } as never);
    vi.spyOn(hooks, "useForecastHeatmap").mockReturnValue({ data: undefined, isPending: false, error: null, refetch: vi.fn() } as never);
    renderAnalysis("/agencies/1/analysis/when?report=route_forecast&time_band=morning", ["dow_weekday", "route_forecast"]);
    expect(screen.getByRole("button", { name: "Morning (05–09)" })).toHaveClass("scope-token--off");
  });

  it("counts a chosen route as used on the route forecast, whose route view honours it", () => {
    mockSupportHooks();
    vi.spyOn(hooks, "useReports").mockReturnValue({ data: [], isLoading: false, error: null, refetch: vi.fn() } as never);
    vi.spyOn(hooks, "useReport").mockReturnValue({ data: undefined, isFetching: false, error: null, refetch: vi.fn() } as never);
    vi.spyOn(hooks, "useForecastOverview").mockReturnValue({
      data: {
        grid: [],
        worst: null,
        routes: [],
        forecast_days: [],
        history_from: null,
        history_to: null,
        recent_from: null,
        recent_to: null,
        disclaimer: "",
        scope_applied: { from: false, to: false, dow: false, time_band: false, routes: false },
      },
      isPending: false,
      error: null,
      refetch: vi.fn(),
    } as never);
    vi.spyOn(hooks, "useForecastHeatmap").mockReturnValue({
      data: {
        route: "44242",
        cells: [],
        forecast_days: [],
        history_from: null,
        history_to: null,
        scope_applied: { from: false, to: false, dow: false, time_band: false, routes: true },
      },
      isPending: false,
      error: null,
      refetch: vi.fn(),
    } as never);
    renderAnalysis("/agencies/1/analysis/when?report=route_forecast&routes=44242", ["route_forecast"]);
    expect(screen.getByText(/Not used on this screen/)).not.toHaveTextContent("Routes");
  });

  it("greys nothing while a different report's response is still on screen", () => {
    mockSupportHooks();
    vi.spyOn(hooks, "useReports").mockReturnValue({ data: [reportMeta("trend"), reportMeta("dow_weekday")], isLoading: false, error: null, refetch: vi.fn() } as never);
    vi.spyOn(hooks, "useReport").mockReturnValue({
      data: { ...reportResponse("dow_weekday"), scope_applied: { from: true, to: true, dow: false } },
      isFetching: true,
      error: null,
      refetch: vi.fn(),
    } as never);
    renderAnalysis("/agencies/1/analysis/when?report=trend&dow=weekday", ["trend", "dow_weekday"]);
    expect(screen.getByRole("button", { name: "weekdays" })).not.toHaveClass("scope-token--off");
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
    await userEvent.click(screen.getByRole("button", { name: /^Routes on weekdays/ }));
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

  it("downloads a report's CSV from the API's origin", () => {
    vi.stubEnv("VITE_API_BASE_URL", "https://api.example.test");
    mockSupportHooks();
    vi.spyOn(hooks, "useReports").mockReturnValue({ data: [reportMeta("ranking")], isLoading: false, error: null, refetch: vi.fn() } as never);
    const ranking = { ...reportResponse("ranking"), rows: [["39061", "平日", 5.2, 3.1, 8.4, 120]] } as ReportResponse; // i18n-ignore: GTFS service name
    vi.spyOn(hooks, "useReport").mockReturnValue({ data: ranking, isFetching: false, error: null, refetch: vi.fn() } as never);
    renderAnalysis("/agencies/1/analysis/rider?report=ranking");
    const csvLink = screen.getAllByRole("link").find((a) => a.getAttribute("href")?.includes("format=csv"));
    expect(csvLink?.getAttribute("href")).toMatch(/^https:\/\/api\.example\.test\/api\/1\/reports\/ranking\?/);
  });

  it("keeps a report's raw rows for admins, out of everyone else's way", () => {
    mockSupportHooks();
    vi.spyOn(hooks, "useReports").mockReturnValue({ data: [reportMeta("ranking")], isLoading: false, error: null, refetch: vi.fn() } as never);
    const ranking = { ...reportResponse("ranking"), rows: [["39061", "平日", 5.2, 3.1, 8.4, 120]] } as ReportResponse; // i18n-ignore: GTFS service name
    vi.spyOn(hooks, "useReport").mockReturnValue({ data: ranking, isFetching: false, error: null, refetch: vi.fn() } as never);
    const isAdmin = vi.spyOn(adminHook, "useIsAdmin").mockReturnValue(false);
    renderAnalysis("/agencies/1/analysis/rider?report=ranking");
    expect(screen.queryByText(/Raw \(/)).not.toBeInTheDocument();
    cleanup();
    isAdmin.mockReturnValue(true);
    renderAnalysis("/agencies/1/analysis/rider?report=ranking");
    expect(screen.getByText("Raw (1 row)")).toBeInTheDocument();
  });

  it("says a one-day period has no trend instead of drawing a single dot", () => {
    mockSupportHooks();
    vi.spyOn(hooks, "useReports").mockReturnValue({ data: [reportMeta("trend")], isLoading: false, error: null, refetch: vi.fn() } as never);
    const rows: TrendPayload[] = [
      { days: [{ date: "2026-09-28", avg_min: 8.3, samples: 40, top_offenders: [] }], hourly: [], dow_band: { grid: [], worst: null }, revision_boundaries: [] },
    ];
    const trend = { ...reportResponse("trend"), rows } as ReportResponse;
    vi.spyOn(hooks, "useReport").mockReturnValue({ data: trend, isFetching: false, error: null, refetch: vi.fn() } as never);
    renderAnalysis("/agencies/1/analysis/when?report=trend&from=2026-09-28&to=2026-09-28", ["trend"]);
    expect(screen.getByText("A trend needs more than one day. Pick a longer period to see how delay moved from day to day.")).toBeInTheDocument();
    expect(screen.queryByRole("img", { name: /daily/i })).not.toBeInTheDocument();
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

  it("lists dwell and running times one row per route, with no service column or note", () => {
    const route = (route_code: string) => ({
      route_code,
      // The active service filter, the same on every row.
      service_type: "平日",
      dwell_samples: 1,
      dwell_avg_sec: 20,
      dwell_p50_sec: 18,
      dwell_p90_sec: 40,
      run_samples: 1,
      run_avg_sec: 90,
      run_p50_sec: 85,
      run_p90_sec: 130,
    });
    vi.spyOn(hooks, "useReports").mockReturnValue({ data: [], isLoading: false, error: null } as never);
    vi.spyOn(hooks, "useReport").mockReturnValue({
      data: {
        ...emptyReport("dwell_run"),
        rows: [{ available: true, time_band_supported: true, routes: [route("R1"), route("R2")] }],
      },
      isPending: false,
      error: null,
      refetch: vi.fn(),
    } as never);
    vi.spyOn(hooks, "useAgencies").mockReturnValue({ data: [], isPending: false } as never);
    vi.spyOn(hooks, "useRoutes").mockReturnValue({ data: [], isLoading: false } as never);
    renderWithProviders(
      <MemoryRouter initialEntries={["/agencies/8/analysis/why?report=dwell_run&from=2020-01-01&to=2020-01-07&service=平日"]}>
        <Routes>
          <Route path="/agencies/:agencyId/analysis/:lens" element={<AnalysisTab reportTypes={["dwell_run"]} />} />
        </Routes>
      </MemoryRouter>,
    );
    const table = screen.getByRole("table");
    expect(within(table).getAllByText(/^Route R\d$/)).toHaveLength(2);
    expect(within(table).queryByRole("columnheader", { name: "Service" })).toBeNull();
    expect(within(table).queryByText("Weekday")).toBeNull();
    expect(screen.queryByText(/Service: the timetable the trip ran on/)).toBeNull();
  });

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
    // The cap is 200 rows, so proving it renders a few hundred table rows
    // four times; on a loaded machine that alone outlasts the default timeout.
  }, 45_000);
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

  it("draws compare_ranking as morphing bars rather than a table", () => {
    show("/agencies/1/analysis/rider", "compare_ranking", [["3", 3.4, 2.7, 0.7, 0.7], ["12", 3.8, 3.1, 0.7, 0.7]]);
    expect(screen.getAllByTestId("compare-bar-row")).toHaveLength(2);
    expect(screen.queryByRole("table")).toBeNull();
  });
});

describe("AnalysisTab route list coverage", () => {
  afterEach(() => {
    vi.restoreAllMocks();
  });

  function dowResponse(overrides: Record<string, unknown> = {}): ReportResponse {
    return {
      ...reportResponse("dow_weekday"),
      rows: [
        ["39061", null, "平日", 5.2, 120], // i18n-ignore: GTFS service name
        ["39062", null, "平日", 4.0, 110], // i18n-ignore: GTFS service name
      ],
      ...overrides,
    } as ReportResponse;
  }

  function setup(path: string, response: ReportResponse, reportTypes: readonly string[] = ["dow_weekday"]) {
    mockSupportHooks();
    vi.spyOn(hooks, "useReports").mockReturnValue({
      data: reportTypes.map((type) => reportMeta(type as ReportType)),
      isLoading: false,
      error: null,
      refetch: vi.fn(),
    } as never);
    const useReport = vi
      .spyOn(hooks, "useReport")
      .mockReturnValue({ data: response, isFetching: false, error: null, refetch: vi.fn() } as never);
    const { router } = renderAnalysis(path, reportTypes);
    return { useReport, router };
  }

  it("says how many retired route codes a list left out, and keeps them on request", async () => {
    const { useReport, router } = setup("/agencies/1/analysis/when?report=dow_weekday", dowResponse({ retired_rows: 2 }));
    const toggle = screen.getByRole("checkbox", { name: "Include 2 route codes the current timetable no longer runs" });
    expect(useReport.mock.calls.at(-1)?.[3]).toMatchObject({ includeRetired: false });
    await userEvent.click(toggle);
    expect(new URLSearchParams(router.state.location.search).get("retired")).toBe("1");
    expect(useReport.mock.calls.at(-1)?.[3]).toMatchObject({ includeRetired: true });
  });

  it("offers no retired-route toggle when a list left none out", () => {
    setup("/agencies/1/analysis/when?report=dow_weekday", dowResponse({ retired_rows: 0 }));
    expect(screen.queryByRole("checkbox", { name: /no longer runs/ })).not.toBeInTheDocument();
  });

  it("exports the retired route codes when the table shows them", () => {
    setup("/agencies/1/analysis/when?report=dow_weekday&retired=1", dowResponse({ retired_rows: 1 }));
    // One code reads in the singular, and the count holds while they are kept.
    expect(screen.getByRole("checkbox", { name: "Include 1 route code the current timetable no longer runs" })).toBeChecked();
    expect(screen.getByRole("link", { name: /CSV/ }).getAttribute("href")).toMatch(/[?&]include_retired=1(&|$)/);
  });

  it("says how many zero-delay routes a list left out, and keeps them on request", async () => {
    const { useReport, router } = setup(
      "/agencies/1/analysis/when?report=dow_weekday",
      dowResponse({ retired_rows: 0, zero_delay_rows: 2 }),
    );
    const toggle = screen.getByRole("checkbox", { name: "Include 2 routes whose feeds always report zero delay" });
    expect(useReport.mock.calls.at(-1)?.[3]).toMatchObject({ includeZeroDelay: false });
    await userEvent.click(toggle);
    expect(new URLSearchParams(router.state.location.search).get("zero_delay")).toBe("1");
    expect(useReport.mock.calls.at(-1)?.[3]).toMatchObject({ includeZeroDelay: true, includeRetired: false });
  });

  it("names its export Download CSV, as every other export is named", () => {
    setup("/agencies/1/analysis/when?report=dow_weekday", dowResponse());
    expect(screen.getByRole("link", { name: "Download CSV" })).toBeInTheDocument();
  });

  it("offers no sort, order or observation floor: those belong to the Routes table", () => {
    const { useReport } = setup("/agencies/1/analysis/when?report=dow_weekday&sparse=1&order=best", dowResponse());
    expect(screen.queryByRole("radio")).not.toBeInTheDocument();
    expect(screen.queryByRole("checkbox", { name: /observed fewer than/ })).not.toBeInTheDocument();
    expect(useReport.mock.calls.at(-1)?.[3]).toEqual({ includeRetired: false, includeZeroDelay: false });
    expect(screen.getByRole("link", { name: /CSV/ }).getAttribute("href") ?? "").not.toMatch(/include_sparse|order=/);
  });

  it("says when the compare list stops short of every compared route, with no longer list to offer", () => {
    const rows = [["3", 3.4, 2.7, 0.7, 0.7], ["12", 3.8, 3.1, 0.7, 0.7]];
    setup(
      "/agencies/1/analysis/rider?report=compare_ranking",
      { ...reportResponse("compare_ranking"), rows, rows_total: 812 } as ReportResponse,
      ["compare_ranking"],
    );
    expect(screen.getAllByTestId("compare-bar-row")).toHaveLength(2);
    expect(screen.getByText("Showing 2 of 812")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Show all" })).not.toBeInTheDocument();
  });

  it("states no count when the compare list holds every compared route", () => {
    const rows = [["3", 3.4, 2.7, 0.7, 0.7]];
    setup(
      "/agencies/1/analysis/rider?report=compare_ranking",
      { ...reportResponse("compare_ranking"), rows, rows_total: 1 } as ReportResponse,
      ["compare_ranking"],
    );
    expect(screen.queryByText(/^Showing/)).not.toBeInTheDocument();
  });

  it("asks for the API's longest compare list, since the bars re-rank it by period rather than by gap", () => {
    const { useReport } = setup("/agencies/1/analysis/rider?report=compare_ranking", reportResponse("compare_ranking"), ["compare_ranking"]);
    expect(useReport.mock.calls.at(-1)?.[3]).toEqual({ limit: 500, includeRetired: false, includeZeroDelay: false });
    const href = screen.getByRole("link", { name: /CSV/ }).getAttribute("href") ?? "";
    expect(href).toMatch(/[?&]limit=500(&|$)/);
    expect(href).not.toContain("include_sparse");
  });
});

describe("AnalysisTab Why without arrival times", () => {
  afterEach(() => {
    vi.restoreAllMocks();
  });

  function showUnavailableDwellRun() {
    mockSupportHooks();
    vi.spyOn(hooks, "useAgencies").mockReturnValue({ data: [], isPending: false } as never);
    vi.spyOn(hooks, "useReports").mockReturnValue({ data: [reportMeta("dwell_run")], isLoading: false, error: null, refetch: vi.fn() } as never);
    vi.spyOn(hooks, "useReport").mockReturnValue({
      data: {
        ...emptyReport("dwell_run"),
        rows: [
          {
            available: false,
            time_band_supported: true,
            routes: [],
            unavailable_reason: "probe_expired",
            last_checked: "2026-09-11",
          },
        ],
      },
      isFetching: false,
      error: null,
      refetch: vi.fn(),
    } as never);
    return renderAnalysis("/agencies/1/analysis/why?report=dwell_run&from=2026-09-01&to=2026-09-07", ["dwell_run"]);
  }

  it("says why the split can't be shown and where to look instead", async () => {
    const { router } = showUnavailableDwellRun();
    const dwell = within(screen.getByRole("region", { name: "Analyses not shown" })).getByRole("listitem", {
      name: "Dwell/running time",
    });
    expect(within(dwell).getByText("Needs re-check")).toBeInTheDocument();
    expect(within(dwell).getByText(/re-checked \(last checked Sep 11, 2026\)/)).toBeInTheDocument();
    expect(screen.queryByText(/departures only/)).not.toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "See when delay happens" }));
    expect(router.state.location.pathname).toBe("/agencies/1/time");
    expect(new URLSearchParams(router.state.location.search).get("from")).toBe("2026-09-01");
  });

  it("points to the weekday and weekend/holiday comparison too", async () => {
    const { router } = showUnavailableDwellRun();
    await userEvent.click(screen.getByRole("button", { name: "Compare weekdays with weekends and holidays" }));
    expect(router.state.location.pathname).toBe("/agencies/1/compare");
    expect(new URLSearchParams(router.state.location.search).get("report")).toBe("compare_ranking");
  });

  it("offers no CSV of a split it can't make", () => {
    showUnavailableDwellRun();
    expect(screen.queryByRole("link", { name: /CSV/ })).not.toBeInTheDocument();
  });
});

describe("AnalysisTab council report", () => {
  afterEach(() => {
    vi.restoreAllMocks();
  });

  const ALL_SIX = { from: true, to: true, dow: true, time_band: true, service: true, routes: true };
  const DATES_ONLY = { from: true, to: true, dow: false, time_band: false, service: false, routes: false };

  function showCouncil(routes: string[] = [], extra: Record<string, unknown> = {}) {
    mockSupportHooks();
    vi.spyOn(hooks, "useReports").mockReturnValue({ data: [reportMeta("council_summary")], isLoading: false, error: null, refetch: vi.fn() } as never);
    vi.spyOn(hooks, "useReport").mockReturnValue({
      data: {
        ...reportResponse("council_summary"),
        rows: [[22.6, 2.5, 540357, 2130, 2100, 98.6]],
        text: "Aomori, 9/1 – 9/28\nOn time 22.6%, average delay 2.5 min.\n* On time means within 1 min.",
        ctx: { from: "2026-09-01", to: "2026-09-28", dow: "all", time_band: "all", service: "all", routes },
        scope_applied: ALL_SIX,
        delivered_scope_applied: DATES_ONLY,
        covered_days: 28,
        window_days: 28,
        delivered_missing: null,
        ...extra,
      },
      isFetching: false,
      error: null,
      refetch: vi.fn(),
    } as never);
    return renderAnalysis("/agencies/1/analysis/reports?report=council_summary", ["council_summary"]);
  }

  it("lays the council report out as figures and prose, not a one-row table", () => {
    showCouncil();
    const figures = screen.getByRole("list", { name: "Headline figures" });
    expect(figures).toHaveTextContent("22.6%");
    expect(figures).toHaveTextContent("2.5 min");
    expect(figures).toHaveTextContent("540,357");
    expect(figures).toHaveTextContent("2,130");
    expect(screen.getByText("On time 22.6%, average delay 2.5 min.")).toBeInTheDocument();
    expect(screen.queryByRole("grid")).toBeNull();
    expect(screen.queryByRole("table")).toBeNull();
  });

  it("labels planned trips as the whole agency's only when a route filter narrows the on-time figures", () => {
    showCouncil();
    expect(screen.getByRole("list", { name: "Headline figures" })).not.toHaveTextContent("whole agency");
    cleanup();
    showCouncil(["R1"]);
    expect(screen.getByRole("list", { name: "Headline figures" })).toHaveTextContent("Planned trips (whole agency)");
  });

  function figure(label: string) {
    const list = screen.getByRole("list", { name: "Headline figures" });
    return within(list).getByText(label).closest("li")!;
  }

  it("names the observation count as stop-level readings, not departures", () => {
    showCouncil();
    expect(figure("Observations (stop level)")).toHaveTextContent("540,357");
    expect(screen.queryByText("Departures observed")).toBeNull();
  });

  it("says how many of the period's days the planned trips cover, in the agencies board's words", () => {
    showCouncil([], { rows: [[22.6, 2.5, 540357, 57887, null, null]], covered_days: 16, window_days: 30 });
    expect(figure("Planned trips")).toHaveTextContent("57,887");
    expect(figure("Planned trips")).toHaveTextContent("16/30 days a timetable plans");
    cleanup();
    showCouncil();
    expect(figure("Planned trips")).not.toHaveTextContent("days a timetable plans");
  });

  it("shows the service-delivered rate, or why it is missing in the agencies board's words", () => {
    showCouncil();
    expect(figure("Service delivered %")).toHaveTextContent("98.6%");
    cleanup();
    showCouncil([], { rows: [[22.6, 2.5, 540357, 2130, null, null]], delivered_missing: "feed_unverified" });
    expect(figure("Service delivered %")).toHaveTextContent("—");
    expect(figure("Service delivered %")).toHaveTextContent("the feed is not yet checked for cancellations");
  });

  it("keeps each figure on one line, so a phone never breaks a number in two", () => {
    const css = readFileSync(resolve(__dirname, "../components/analysis/CouncilSummaryBlock.css"), "utf8");
    expect(decl(ruleBody(css, ".council-report__value"), "white-space")).toBe("nowrap");
  });

  it("offers to print the report or save it as a PDF", async () => {
    const print = vi.spyOn(window, "print").mockImplementation(() => {});
    showCouncil();
    await userEvent.click(screen.getByRole("button", { name: "Print / Save as PDF" }));
    expect(print).toHaveBeenCalledOnce();
  });
});

describe("AnalysisTab delay record", () => {
  afterEach(() => {
    vi.restoreAllMocks();
  });

  // The lookup fetches one route; the staff list fetches every route.
  const fetchesStaffList = ([, type, scope]: Parameters<typeof hooks.useReport>) =>
    type === "delay_certificate" && scope.routes.length === 0;

  const ROW = ["Aomori City Bus", "W54", "weekday", "2026-09-02", "14:45:00", "14:50:55", 355, "Aomori Station", true];

  function showCertificate(types: ReportType[] = ["delay_certificate"]) {
    mockSupportHooks();
    vi.spyOn(hooks, "useReports").mockReturnValue({ data: types.map(reportMeta), isLoading: false, error: null, refetch: vi.fn() } as never);
    vi.spyOn(hooks, "useReport").mockImplementation(
      (_aid, reportType) =>
        (reportType == null
          ? { data: undefined, isFetching: false, isPlaceholderData: false, error: null, refetch: vi.fn() }
          : {
              data: {
                ...reportResponse("delay_certificate"),
                rows: [ROW],
                text: "1 departure left more than 300 s late.",
                ctx: { from: "2026-09-02", to: "2026-09-02" },
              },
              isFetching: false,
              isPlaceholderData: false,
              error: null,
              refetch: vi.fn(),
            }) as never,
    );
    return renderAnalysis("/agencies/1/analysis/reports?report=delay_certificate", types);
  }

  it("opens on the passenger's lookup, with the day's late departures folded away until asked for", async () => {
    showCertificate();
    expect(screen.getByLabelText("Date")).toBeInTheDocument();
    const summary = screen.getByText("Late departures");
    const staff = summary.closest("details")!;
    expect(staff).not.toHaveAttribute("open");
    expect(screen.queryByRole("table")).toBeNull();
    await userEvent.click(summary);
    expect(staff).toContainElement(screen.getByRole("table"));
    expect(staff).toContainElement(screen.getByRole("link", { name: /CSV/ }));
    expect(staff).toHaveTextContent("1 departure left more than 300 s late.");
  });

  it("counts the late departures only once staff open them, and only for the lookup's day", async () => {
    showCertificate();
    const asked = () => vi.mocked(hooks.useReport).mock.calls.filter(fetchesStaffList);
    expect(asked()).toEqual([]);
    await userEvent.click(screen.getByText("Late departures"));
    const day = (screen.getByLabelText("Date") as HTMLInputElement).value;
    expect(asked().at(-1)![2]).toMatchObject({ from: day, to: day, routes: [] });
    expect(screen.getByRole("link", { name: /CSV/ }).getAttribute("href")).toContain(`from=${day}&to=${day}`);
  });

  it("follows the day the passenger picks", async () => {
    showCertificate();
    await userEvent.click(screen.getByText("Late departures"));
    fireEvent.change(screen.getByLabelText("Date"), { target: { value: "2026-08-20" } });
    expect(vi.mocked(hooks.useReport).mock.calls.filter(fetchesStaffList).at(-1)![2]).toMatchObject({
      from: "2026-08-20",
      to: "2026-08-20",
    });
  });

  it("starts folded again after staff leave the certificate, without counting the list", async () => {
    const { router } = showCertificate(["council_summary", "delay_certificate"]);
    await userEvent.click(screen.getByText("Late departures"));
    await act(() => router.navigate("/agencies/1/analysis/reports?report=council_summary"));
    const before = vi.mocked(hooks.useReport).mock.calls.length;
    await act(() => router.navigate("/agencies/1/analysis/reports?report=delay_certificate"));
    const asked = vi.mocked(hooks.useReport).mock.calls.slice(before).filter(fetchesStaffList);
    expect(asked).toEqual([]);
    expect(screen.getByText("Late departures").closest("details")).not.toHaveAttribute("open");
  });

  it("leaves the screen's period sentence off the record, whose own day and route are its scope", async () => {
    const { router } = showCertificate(["council_summary", "delay_certificate"]);
    expect(document.querySelector(".scope-sentence")).toBeNull();
    await act(() => router.navigate("/agencies/1/analysis/reports?report=council_summary"));
    expect(document.querySelector(".scope-sentence")).not.toBeNull();
  });

  it("keeps the day's list on screen while a changed day counts", async () => {
    mockSupportHooks();
    vi.spyOn(hooks, "useReports").mockReturnValue({ data: [reportMeta("delay_certificate")], isLoading: false, error: null, refetch: vi.fn() } as never);
    vi.spyOn(hooks, "useReport").mockImplementation(
      (_aid, reportType) =>
        ({
          data:
            reportType == null
              ? undefined
              : {
                  ...reportResponse("delay_certificate"),
                  rows: [ROW],
                  text: "1 departure left more than 300 s late.",
                },
          isFetching: reportType != null,
          isPlaceholderData: reportType != null,
          error: null,
          refetch: vi.fn(),
        }) as never,
    );
    renderAnalysis("/agencies/1/analysis/reports?report=delay_certificate", ["delay_certificate"]);
    const summary = screen.getByText("Late departures");
    await userEvent.click(summary);
    expect(summary.closest("details")).toContainElement(screen.getByRole("table"));
  });

  it("keeps the passenger's lookup when the day's list fails", async () => {
    mockSupportHooks();
    vi.spyOn(hooks, "useReports").mockReturnValue({ data: [reportMeta("delay_certificate")], isLoading: false, error: null, refetch: vi.fn() } as never);
    vi.spyOn(hooks, "useReport").mockReturnValue({
      data: undefined,
      isFetching: false,
      isPlaceholderData: false,
      error: new Error("query timed out"),
      refetch: vi.fn(),
    } as never);
    renderAnalysis("/agencies/1/analysis/reports?report=delay_certificate", ["delay_certificate"]);
    expect(screen.getByLabelText("Date")).toBeInTheDocument();
    const summary = screen.getByText("Late departures");
    await userEvent.click(summary);
    expect(summary.closest("details")).toContainElement(screen.getByRole("alert"));
  });

  it("prints a report without the list, the insights or the staff list", () => {
    const print = ruleBody(readFileSync(resolve(__dirname, "./analysisTab.css"), "utf8"), "@media print");
    expect(decl(ruleBody(print, ".analysis-report-list"), "display")).toBe("none !important");
    expect(decl(ruleBody(print, ".analysis-insights"), "display")).toBe("none !important");
    expect(decl(ruleBody(print, ".cert-staff"), "display")).toBe("none");
  });
});

describe("AnalysisTab slow reports", () => {
  afterEach(() => {
    vi.useRealTimers();
    vi.restoreAllMocks();
  });

  it("says a slow report is still working, and offers the period's last week", () => {
    vi.useFakeTimers();
    mockSupportHooks();
    vi.spyOn(hooks, "useReports").mockReturnValue({ data: [reportMeta("ranking")], isLoading: false, error: null, refetch: vi.fn() } as never);
    vi.spyOn(hooks, "useReport").mockReturnValue({ data: undefined, isFetching: true, error: null, refetch: vi.fn() } as never);
    const { router } = renderAnalysis("/agencies/1/analysis/compare?report=ranking&from=2026-09-01&to=2026-09-30", ["ranking"]);
    act(() => vi.advanceTimersByTime(STILL_WORKING_AFTER_MS));
    expect(screen.getByText("Still working: a long period takes longer to count.")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Narrow to the last 7 days of the period" }));
    const params = new URLSearchParams(router.state.location.search);
    expect([params.get("from"), params.get("to")]).toEqual(["2026-09-24", "2026-09-30"]);
  });
});
