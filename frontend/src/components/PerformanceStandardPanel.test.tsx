import { describe, it, expect, vi, afterEach } from "vitest";
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

function onTimeRow(route_code: string, actual: number | null, meets: boolean | null): PerformanceStandardRow {
  return {
    route_code,
    metric_type: "on_time_pct",
    metric_scope: "route",
    threshold_value: 85,
    bonus_malus_rate: 0,
    actual_value: actual,
    achievement_rate: actual == null ? null : actual / 85,
    estimated_bonus_deduction: actual == null ? null : 0,
    meets,
  };
}

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
            metric_scope: "route",
            threshold_value: 50,
            bonus_malus_rate: 1000,
            actual_value: 50,
            achievement_rate: 1.0,
            estimated_bonus_deduction: 0,
            meets: true,
          },
          {
            route_code: "200",
            metric_type: "ewt_sec",
            metric_scope: "route",
            threshold_value: 100,
            bonus_malus_rate: 1000,
            actual_value: 150,
            achievement_rate: 0.5,
            estimated_bonus_deduction: -500,
            meets: false,
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
          metric_scope: "route",
          threshold_value: 50,
          bonus_malus_rate: 1000,
          actual_value: 50.00002,
          achievement_rate: 0.99999,
          estimated_bonus_deduction: -0.04,
          meets: false,
        },
      ]),
    });
    renderWithProviders(<PerformanceStandardPanel aid={1} ctx={CTX as never} />);
    expect(screen.getByText("0.0")).toBeInTheDocument();
    expect(screen.queryByText("−0.0")).toBeNull();
  });

  it("labels an agency-scoped row instead of implying a route-specific figure", () => {
    mockRoutes();
    mockPerformanceStandards({
      data: response([
        {
          route_code: "300",
          metric_type: "vehicle_km_delivered_pct",
          metric_scope: "agency",
          threshold_value: 90,
          bonus_malus_rate: 2000,
          actual_value: 80,
          achievement_rate: (80 - 90) / 90 + 1,
          estimated_bonus_deduction: 2000 * ((80 - 90) / 90),
          meets: false,
        },
      ]),
    });
    renderWithProviders(<PerformanceStandardPanel aid={1} ctx={CTX as never} />);

    expect(screen.getByText("Agency-wide figure (no per-route breakdown available)")).toBeInTheDocument();
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

  it("names the definition the on-time targets were judged on", () => {
    mockRoutes();
    mockPerformanceStandards({ data: response([onTimeRow("100", 91.2, true)], { on_time_definition: INDUSTRY }) });
    renderWithProviders(<PerformanceStandardPanel aid={1} ctx={CTX as never} />);

    expect(
      screen.getByText("On-time standards are judged on this page's period and filters, under the definition standard_m1_p5:"),
    ).toBeInTheDocument();
    expect(screen.getByText("On time = no more than 5 min late and no more than 1 min early.")).toBeInTheDocument();
  });

  it("names tolerances no preset matches as custom", () => {
    mockRoutes();
    mockPerformanceStandards({
      data: response([onTimeRow("100", 91.2, true)], {
        on_time_definition: { ...INDUSTRY, preset: "custom", early_tolerance_sec: null, late_tolerance_sec: 120 },
      }),
    });
    renderWithProviders(<PerformanceStandardPanel aid={1} ctx={CTX as never} />);

    expect(screen.getByText(/under custom tolerances:$/)).toBeInTheDocument();
    expect(screen.getByText("On time = no more than 2 min late (early departures count as on time).")).toBeInTheDocument();
  });

  it("leaves the on-time definition out when no on-time target is shown", () => {
    mockRoutes();
    mockPerformanceStandards({
      data: response(
        [
          onTimeRow("100", 91.2, true),
          {
            route_code: "200",
            metric_type: "ewt_sec",
            metric_scope: "route",
            threshold_value: 50,
            bonus_malus_rate: 0,
            actual_value: 40,
            achievement_rate: 1.2,
            estimated_bonus_deduction: 0,
            meets: true,
          },
        ],
        { on_time_definition: INDUSTRY },
      ),
    });
    renderWithProviders(<PerformanceStandardPanel aid={1} ctx={CTX as never} route="200" />);

    expect(screen.getByText("Route 200")).toBeInTheDocument();
    expect(screen.queryByText("Route 100")).toBeNull();
    expect(screen.queryByText(/^On time =/)).toBeNull();
  });

  it("on a route's own page shows that route's targets only, and nothing when it has none", () => {
    mockRoutes();
    mockPerformanceStandards({
      data: response([onTimeRow("100", 91.2, true), onTimeRow("200", 70, false)], { on_time_definition: INDUSTRY }),
    });
    const { unmount } = renderWithProviders(<PerformanceStandardPanel aid={1} ctx={CTX as never} route="200" />);
    expect(screen.getByText("Route 200")).toBeInTheDocument();
    expect(screen.queryByText("Route 100")).toBeNull();
    unmount();

    const { container } = renderWithProviders(<PerformanceStandardPanel aid={1} ctx={CTX as never} route="999" />);
    expect(container).toBeEmptyDOMElement();
    expect(screen.queryByText("Minimum performance standard")).toBeNull();
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
