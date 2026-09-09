import { describe, it, expect, vi, afterEach } from "vitest";
import { screen } from "@testing-library/react";
import { renderWithProviders } from "../test/renderWithProviders";
import { PerformanceStandardPanel } from "./PerformanceStandardPanel";
import * as hooks from "../api/hooks";
import type { PerformanceStandardsResponse } from "../api/types";

const CTX = { from: "2026-04-01", to: "2026-04-01", dow: "all", time_band: "all", service: "all", routes: [] } as const;

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

afterEach(() => {
  vi.restoreAllMocks();
});

describe("PerformanceStandardPanel", () => {
  it("renders the simulation badge and disclaimer alongside the achievement table", () => {
    mockRoutes();
    mockPerformanceStandards({
      data: {
        rows: [
          {
            route_code: "100",
            metric_type: "ewt_sec",
            metric_scope: "route",
            threshold_value: 50,
            bonus_malus_rate: 1000,
            actual_value: 50,
            achievement_rate: 1.0,
            estimated_bonus_deduction: 0,
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
          },
        ],
        ctx: CTX,
        disclaimer: "This is an internal simulation/estimate, not an actual invoice or contractual output.",
      },
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

  it("labels an agency-scoped row instead of implying a route-specific figure", () => {
    mockRoutes();
    mockPerformanceStandards({
      data: {
        rows: [
          {
            route_code: "300",
            metric_type: "vehicle_km_delivered_pct",
            metric_scope: "agency",
            threshold_value: 90,
            bonus_malus_rate: 2000,
            actual_value: 80,
            achievement_rate: (80 - 90) / 90 + 1,
            estimated_bonus_deduction: 2000 * ((80 - 90) / 90),
          },
        ],
        ctx: CTX,
        disclaimer: "simulation only",
      },
    });
    renderWithProviders(<PerformanceStandardPanel aid={1} ctx={CTX as never} />);

    expect(screen.getByText("Agency-wide figure (no per-route breakdown available)")).toBeInTheDocument();
  });

  it("renders nothing when this agency has zero configured standards", () => {
    mockRoutes();
    mockPerformanceStandards({ data: { rows: [], ctx: CTX, disclaimer: "simulation only" } });
    const { container } = renderWithProviders(<PerformanceStandardPanel aid={1} ctx={CTX as never} />);
    expect(container).toBeEmptyDOMElement();
  });

  it("shows a skeleton while loading", () => {
    mockRoutes();
    mockPerformanceStandards({ isLoading: true });
    const { container } = renderWithProviders(<PerformanceStandardPanel aid={1} ctx={CTX as never} />);
    expect(container.querySelector(".skeleton")).toBeInTheDocument();
  });
});
