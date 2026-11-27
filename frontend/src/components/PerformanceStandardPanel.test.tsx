import { describe, it, expect, vi, afterEach } from "vitest";
import i18n from "../i18n";
import { screen, within } from "@testing-library/react";
import { renderWithProviders } from "../test/renderWithProviders";
import { PerformanceStandardPanel } from "./PerformanceStandardPanel";
import * as hooks from "../api/hooks";
import type { DefinitionMeta, PerformanceStandardRow, PerformanceStandardsResponse } from "../api/types";

const CTX = { from: "2026-04-01", to: "2026-04-01", dow: "all", time_band: "all", service: "all", routes: [] } as const;

const INDUSTRY: DefinitionMeta = {
  preset: "standard_m1_p5",
  early_tolerance_sec: 60,
  late_tolerance_sec: 300,
  early_departure_sec: null,
  exclusion_threshold_sec: 7200,
  measurement_point: "all_stops_all_observations",
  dedup_rule: "latest_observation_per_stop_event",
  method_version: "2026.11.0",
  source: "aggregates",
  built_at: null,
  value_basis: "feed_prediction",
  timetable_versions: null,
  source_mix: null,
};

function mockRoutes() {
  // The agency catalogue answered without a tier for this agency, unless a test set one.
  if (!vi.isMockFunction(hooks.useAgencies)) vi.spyOn(hooks, "useAgencies").mockReturnValue({ data: [], isPending: false } as never);
  vi.spyOn(hooks, "useRoutes").mockReturnValue({ data: [], isLoading: false } as never);
}

function mockPerformanceStandards(
  result: Partial<{ data: PerformanceStandardsResponse; isLoading: boolean; error: unknown }>,
) {
  vi.spyOn(hooks, "usePerformanceStandards").mockReturnValue({
    data: undefined,
    isLoading: false,
    error: null,
    refetch: vi.fn(),
    ...result,
  } as never);
}

function response(rows: PerformanceStandardRow[], extra: Partial<PerformanceStandardsResponse> = {}): PerformanceStandardsResponse {
  return { rows, ctx: CTX, disclaimer: "simulation only", on_time_definition: null, ...extra };
}

function onTimeRow(
  route_code: string,
  actual: number | null,
  meets: boolean | null,
  not_judged: PerformanceStandardRow["not_judged"] = null,
): PerformanceStandardRow {
  return {
    route_code,
    metric_type: "on_time_pct",
    threshold_value: 85,
    bonus_malus_rate: 0,
    actual_value: actual,
    achievement_rate: actual == null ? null : actual / 85,
    estimated_bonus_deduction: actual == null ? null : 0,
    meets,
    not_judged,
  };
}

// Mocks are restored while the last render is still mounted (RTL's cleanup
// runs after this file's hooks), so nothing here may re-render it after: a
// re-render then reaches the real data hooks the mocks stood in for.
afterEach(() => {
  vi.restoreAllMocks();
});

describe("PerformanceStandardPanel", () => {
  it("renders the simulation badge and disclaimer alongside the achievement table", () => {
    mockRoutes();
    mockPerformanceStandards({
      data: response(
        [
          {
            route_code: "100",
            metric_type: "ewt_sec",
            threshold_value: 50,
            bonus_malus_rate: 1000,
            actual_value: 50,
            achievement_rate: 1.0,
            estimated_bonus_deduction: 0,
            meets: true,
            not_judged: null,
          },
          {
            route_code: "200",
            metric_type: "ewt_sec",
            threshold_value: 100,
            bonus_malus_rate: 1000,
            actual_value: 150,
            achievement_rate: 0.5,
            estimated_bonus_deduction: -500,
            meets: false,
            not_judged: null,
          },
        ],
        { disclaimer: "This is an internal simulation/estimate, not an actual invoice or contractual output." },
      ),
    });
    renderWithProviders(<PerformanceStandardPanel aid={1} ctx={CTX as never} />);

    expect(screen.getByText("Internal simulation — not an invoice")).toBeInTheDocument();
    expect(screen.getByText(/internal simulation\/estimate, not an actual invoice/i)).toBeInTheDocument();
    expect(screen.getByText("Route 100")).toBeInTheDocument();
    expect(screen.getByText("Route 200")).toBeInTheDocument();
    expect(screen.getByText("100.0%")).toBeInTheDocument(); // R100 achievement rate
    expect(screen.getByText("0.0")).toBeInTheDocument(); // R100 zero estimate
    expect(screen.getByText("50.0%")).toBeInTheDocument(); // R200 achievement rate
    expect(screen.getByText("−500.0")).toBeInTheDocument(); // R200 deduction estimate
  });

  it("prints an estimate that rounds to zero without a minus sign", () => {
    mockRoutes();
    mockPerformanceStandards({
      data: response([
        {
          route_code: "100",
          metric_type: "ewt_sec",
          threshold_value: 50,
          bonus_malus_rate: 1000,
          actual_value: 50.00002,
          achievement_rate: 0.99999,
          estimated_bonus_deduction: -0.04,
          meets: false,
          not_judged: null,
        },
      ]),
    });
    renderWithProviders(<PerformanceStandardPanel aid={1} ctx={CTX as never} />);
    expect(screen.getByText("0.0")).toBeInTheDocument();
    expect(screen.queryByText("−0.0")).toBeNull();
  });

  it("judges a service-delivered target on the route's own share", () => {
    mockRoutes();
    mockPerformanceStandards({
      data: response([
        {
          route_code: "300",
          metric_type: "service_delivered_pct",
          threshold_value: 98,
          bonus_malus_rate: 0,
          actual_value: 99.2,
          achievement_rate: 99.2 / 98,
          estimated_bonus_deduction: 0,
          meets: true,
          not_judged: null,
        },
      ]),
    });
    renderWithProviders(<PerformanceStandardPanel aid={1} ctx={CTX as never} />);

    const [, row] = screen.getAllByRole("row");
    expect(within(row).getByText("Service delivered (%)")).toBeInTheDocument();
    expect(within(row).getByText("98.0%")).toBeInTheDocument();
    expect(within(row).getByText("99.2%")).toBeInTheDocument();
    expect(within(row).getByText("Meets standard")).toHaveClass("standard-chip", "standard-chip--meets");
    expect(
      screen.getByText("Service-delivered and vehicle-km standards are judged on each route's own planned trips over this page's period; its other filters don't apply."),
    ).toBeInTheDocument();
    expect(screen.queryByText(/^On-time standards are judged/)).toBeNull();
  });

  it("says why a service-delivered target is not judged, in each case", () => {
    mockRoutes();
    const unjudged = (route_code: string, not_judged: PerformanceStandardRow["not_judged"]): PerformanceStandardRow => ({
      route_code,
      metric_type: "service_delivered_pct",
      threshold_value: 98,
      bonus_malus_rate: 0,
      actual_value: null,
      achievement_rate: null,
      estimated_bonus_deduction: null,
      meets: null,
      not_judged,
    });
    mockPerformanceStandards({
      data: response([
        unjudged("100", { reason: "too_few_planned_trips", observations: null, floor: 100, planned_trips: 42 }),
        unjudged("200", { reason: "route_plan_unknown", observations: null, floor: null, planned_trips: null }),
        unjudged("300", { reason: "feed_reports_no_cancellations", observations: null, floor: null, planned_trips: null }),
        unjudged("400", { reason: "too_few_observations", observations: 7, floor: 100, planned_trips: null }),
        { ...unjudged("500", { reason: "no_shapes", observations: null, floor: null, planned_trips: null }), metric_type: "vehicle_km_delivered_pct" },
      ]),
    });
    renderWithProviders(<PerformanceStandardPanel aid={1} ctx={CTX as never} />);

    const [, fewPlanned, noSplit, noCancellations, thin, noShapes] = screen.getAllByRole("row");
    expect(within(fewPlanned).getByText("Not judged: fewer than 100 planned trips (42)")).toHaveClass("standard-unjudged");
    expect(
      within(noSplit).getByText("Not judged: the timetable's trips by route aren't recorded for every day of this period"),
    ).toBeInTheDocument();
    expect(within(noCancellations).getByText("Not judged: this feed doesn't report cancellations")).toBeInTheDocument();
    expect(within(thin).getByText("Not judged: fewer than 100 observations (7)")).toBeInTheDocument();
    expect(within(noShapes).getByText("Vehicle-km delivered (%)")).toBeInTheDocument();
    expect(within(noShapes).getByText("Not judged: no route shapes loaded")).toBeInTheDocument();
  });

  it("says why a service-delivered target is not judged in Japanese too", async () => {
    await i18n.changeLanguage("ja");
    mockRoutes();
    mockPerformanceStandards({
      data: response([
        {
          route_code: "100",
          metric_type: "service_delivered_pct",
          threshold_value: 98,
          bonus_malus_rate: 0,
          actual_value: null,
          achievement_rate: null,
          estimated_bonus_deduction: null,
          meets: null,
          not_judged: { reason: "too_few_planned_trips", observations: null, floor: 100, planned_trips: 42 },
        },
        {
          route_code: "200",
          metric_type: "service_delivered_pct",
          threshold_value: 98,
          bonus_malus_rate: 0,
          actual_value: null,
          achievement_rate: null,
          estimated_bonus_deduction: null,
          meets: null,
          not_judged: { reason: "feed_unverified", observations: null, floor: null, planned_trips: null },
        },
      ]),
    });
    const { unmount } = renderWithProviders(<PerformanceStandardPanel aid={1} ctx={CTX as never} />);
    try {
      expect(screen.getAllByText("運行率（%）")).toHaveLength(2);
      expect(screen.getByText("計画便数が100便未満のため判定していません（42便）")).toBeInTheDocument();
      expect(screen.getByText("判定していません（配信が運休を伝えるか未確認）")).toBeInTheDocument();
    } finally {
      unmount();
      await i18n.changeLanguage("en");
    }
  });

  it("states a basic-tier agency's tier instead of why its feed can't be judged for delivery", () => {
    mockRoutes();
    vi.spyOn(hooks, "useAgencies").mockReturnValue({
      data: [{ agency_id: 1, agency_name: "Aomori", feed_url: "", static_url: null, latest_data_date: null, capability_tier: "basic" }],
    } as never);
    mockPerformanceStandards({
      data: response([
        {
          route_code: "200",
          metric_type: "service_delivered_pct",
          threshold_value: 98,
          bonus_malus_rate: 0,
          actual_value: null,
          achievement_rate: null,
          estimated_bonus_deduction: null,
          meets: null,
          not_judged: { reason: "feed_cannot_report", observations: null, floor: null, planned_trips: null },
        },
      ]),
    });
    renderWithProviders(<PerformanceStandardPanel aid={1} ctx={CTX as never} />);
    expect(screen.getByText("Not judged: Basic data: on-time only")).toBeInTheDocument();
    expect(screen.queryByText(/cancellations/)).not.toBeInTheDocument();
  });

  it("says whether each on-time rate meets its target, in calm chips", () => {
    mockRoutes();
    mockPerformanceStandards({
      data: response([onTimeRow("100", 91.2, true), onTimeRow("200", 70, false), onTimeRow("300", null, null)], {
        on_time_definition: INDUSTRY,
      }),
    });
    renderWithProviders(<PerformanceStandardPanel aid={1} ctx={CTX as never} />);

    const [, meets, below, unjudged] = screen.getAllByRole("row");
    expect(within(meets).getByText("On-time rate (%)")).toBeInTheDocument();
    expect(within(meets).getByText("85.0%")).toBeInTheDocument();
    expect(within(meets).getByText("91.2%")).toBeInTheDocument();
    expect(within(meets).getByText("Meets standard")).toHaveClass("standard-chip", "standard-chip--meets");
    expect(within(below).getByText("70.0%")).toBeInTheDocument();
    expect(within(below).getByText("Below standard")).toHaveClass("standard-chip", "standard-chip--below");
    expect(within(unjudged).queryByText(/standard$/)).toBeNull();
  });

  it("says why an on-time target is not judged instead of leaving a bare dash", () => {
    mockRoutes();
    mockPerformanceStandards({
      data: response(
        [
          onTimeRow("100", null, null, { reason: "too_few_observations", observations: 42, floor: 100 }),
          onTimeRow("200", null, null, { reason: "zero_delay_feed", observations: null, floor: null }),
        ],
        { on_time_definition: INDUSTRY },
      ),
    });
    renderWithProviders(<PerformanceStandardPanel aid={1} ctx={CTX as never} />);

    const [, thin, zero] = screen.getAllByRole("row");
    expect(within(thin).getByText("Not judged: fewer than 100 observations (42)")).toHaveClass("standard-unjudged");
    expect(within(zero).getByText("Not judged: the feed always reports zero delay")).toHaveClass("standard-unjudged");
  });

  it("says why in Japanese too", async () => {
    await i18n.changeLanguage("ja");
    mockRoutes();
    mockPerformanceStandards({
      data: response(
        [
          onTimeRow("100", null, null, { reason: "too_few_observations", observations: 42, floor: 100 }),
          onTimeRow("200", null, null, { reason: "zero_delay_feed", observations: null, floor: null }),
        ],
        { on_time_definition: INDUSTRY },
      ),
    });
    const { unmount } = renderWithProviders(<PerformanceStandardPanel aid={1} ctx={CTX as never} />);
    try {
      expect(screen.getByText("観測が100回未満のため判定していません（42回）")).toBeInTheDocument();
      expect(screen.getByText("遅れが常に0と報告されるため判定していません")).toBeInTheDocument();
    } finally {
      // Back to English once nothing is mounted to re-render in it.
      unmount();
      await i18n.changeLanguage("en");
    }
  });

  it("names the definition the on-time targets were judged on, in words rather than its key", () => {
    mockRoutes();
    mockPerformanceStandards({ data: response([onTimeRow("100", 91.2, true)], { on_time_definition: INDUSTRY }) });
    const { container } = renderWithProviders(<PerformanceStandardPanel aid={1} ctx={CTX as never} />);

    expect(
      screen.getByText("On-time standards are judged on this page's period and filters, under this definition of on time:"),
    ).toBeInTheDocument();
    expect(screen.getByText("On time (industry standard) = no more than 5 min late and no more than 1 min early.")).toBeInTheDocument();
    expect(container).not.toHaveTextContent("standard_m1_p5");
  });

  it("states custom tolerances the same way", () => {
    mockRoutes();
    mockPerformanceStandards({
      data: response([onTimeRow("100", 91.2, true)], {
        on_time_definition: { ...INDUSTRY, preset: "custom", early_tolerance_sec: null, late_tolerance_sec: 120 },
      }),
    });
    const { container } = renderWithProviders(<PerformanceStandardPanel aid={1} ctx={CTX as never} />);

    expect(screen.getByText("On time = no more than 2 min late (early departures count as on time).")).toBeInTheDocument();
    expect(container).not.toHaveTextContent("custom");
  });

  it("leaves the on-time definition out when no on-time target is shown", () => {
    mockRoutes();
    mockPerformanceStandards({
      data: response(
        [
          {
            route_code: "200",
            metric_type: "ewt_sec",
            threshold_value: 50,
            bonus_malus_rate: 0,
            actual_value: 40,
            achievement_rate: 1.2,
            estimated_bonus_deduction: 0,
            meets: true,
            not_judged: null,
          },
        ],
        { on_time_definition: null },
      ),
    });
    renderWithProviders(<PerformanceStandardPanel aid={1} ctx={CTX as never} />);

    expect(screen.getByText("Route 200")).toBeInTheDocument();
    expect(screen.queryByText(/^On time =/)).toBeNull();
  });

  it("on a route's own page asks for that route's targets alone, and shows nothing when it has none", () => {
    mockRoutes();
    mockPerformanceStandards({ data: response([onTimeRow("200", 70, false)], { on_time_definition: INDUSTRY }) });
    const { unmount } = renderWithProviders(<PerformanceStandardPanel aid={1} ctx={CTX as never} route="200" />);
    expect(hooks.usePerformanceStandards).toHaveBeenCalledWith(1, CTX, true, "200");
    expect(screen.getByText("Route 200")).toBeInTheDocument();
    unmount();

    mockPerformanceStandards({ data: response([]) });
    const { container } = renderWithProviders(<PerformanceStandardPanel aid={1} ctx={CTX as never} route="999" />);
    expect(container).toBeEmptyDOMElement();
  });

  it("calls the same hooks while loading as once the standards arrive", () => {
    mockRoutes();
    const standards = vi.spyOn(hooks, "usePerformanceStandards").mockReturnValue({
      data: undefined,
      isLoading: true,
      error: null,
      refetch: vi.fn(),
    } as never);
    const { container, rerender } = renderWithProviders(<PerformanceStandardPanel aid={1} ctx={CTX as never} />);
    expect(container).toBeEmptyDOMElement();

    standards.mockReturnValue({
      data: response([onTimeRow("100", 91.2, true)], { on_time_definition: INDUSTRY }),
      isLoading: false,
      error: null,
      refetch: vi.fn(),
    } as never);
    rerender(<PerformanceStandardPanel aid={1} ctx={CTX as never} />);
    expect(screen.getByText("Route 100")).toBeInTheDocument();
    expect(screen.getByText("Meets standard")).toBeInTheDocument();
  });

  it("renders nothing when this agency has zero configured standards", () => {
    mockRoutes();
    mockPerformanceStandards({ data: response([]) });
    const { container } = renderWithProviders(<PerformanceStandardPanel aid={1} ctx={CTX as never} />);
    expect(container).toBeEmptyDOMElement();
  });

  it("stays out of the page until it knows there are standards to show", () => {
    mockRoutes();
    mockPerformanceStandards({ isLoading: true });
    const { container } = renderWithProviders(<PerformanceStandardPanel aid={1} ctx={CTX as never} />);
    expect(container).toBeEmptyDOMElement();
  });
});
