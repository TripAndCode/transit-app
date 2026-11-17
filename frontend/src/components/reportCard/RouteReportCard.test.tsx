import { afterEach, describe, expect, it, vi } from "vitest";
import { screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { renderWithProviders } from "../../test/renderWithProviders";
import type { RouteCard, RouteReportCardResponse } from "./reportCardApi";
import { RouteReportCardView } from "./RouteReportCard";

const sub = (metric: RouteCard["subscores"][number]["metric"], value: number | null, letter: RouteCard["grade"], graded: boolean) => ({
  metric,
  value,
  ci_low: value == null || metric === "ewt_min" ? null : value - 1,
  ci_high: value == null || metric === "ewt_min" ? null : value + 1,
  letter,
  graded,
  missing: value == null ? "not_measured" : null,
});

const card: RouteCard = {
  route_code: "50",
  month: "2026-09",
  frequency: "high",
  grade: "B",
  not_graded: null,
  observations: 2000,
  floor: 100,
  trips_observed: 1000,
  planned_trips: 1000,
  coverage_pct: 100,
  on_time_ci_low: 93,
  on_time_ci_high: 96,
  missing: [],
  subscores: [
    sub("on_time_pct", 95, "A", false),
    sub("early_pct", 0.5, "A", false),
    sub("ewt_min", 0.4, "A", true),
    sub("bunching_pct", 9, "B", true),
    sub("service_delivered_pct", 93, "D", true),
  ],
  raise_to: [{ metric: "bunching_pct", value: 9, target: 5 }],
};

function response(over: Partial<RouteReportCardResponse> = {}): RouteReportCardResponse {
  return {
    month: "2026-09",
    latest_month: "2026-10",
    card,
    history: [
      { month: "2026-07", grade: null, not_graded: "no_data" },
      { month: "2026-08", grade: "C", not_graded: null },
      { month: "2026-09", grade: "B", not_graded: null },
    ],
    rules: {
      ewt_min_edges: [0.5, 1, 1.5, 2],
      bunching_pct_edges: [5, 10, 15, 20],
      on_time_pct_edges: [90, 80, 70, 60],
      early_pct_edges: [1, 2, 5, 10],
      truancy_cap_pct: 95,
      truancy_fail_pct: 90,
      min_observations: 100,
    },
    definition: {
      preset: "standard_m1_p5",
      early_tolerance_sec: 60,
      late_tolerance_sec: 300,
      early_departure_sec: 60,
      exclusion_threshold_sec: 7200,
      measurement_point: "all_stops_all_observations",
      dedup_rule: "latest_observation_per_stop_event",
      method_version: "2026.11.20",
      source: "aggregates",
      built_at: null,
      value_basis: "feed_prediction",
    } as never,
    ...over,
  };
}

function render(data: RouteReportCardResponse | undefined, onMonth = vi.fn()) {
  renderWithProviders(<RouteReportCardView data={data} error={null} onRetry={() => {}} month="" onMonth={onMonth} />);
  return onMonth;
}

describe("RouteReportCardView", () => {
  afterEach(() => vi.restoreAllMocks());

  it("shows the grade, what would raise it, and each figure with its interval", () => {
    render(response());
    const region = screen.getByRole("region", { name: "Report card" });
    expect(within(region).getAllByText("B")[0]).toHaveClass("report-grade--large");
    expect(screen.getByText(/For the next grade:/)).toHaveTextContent("Bunching 9.0% → 5.0%");
    const bunching = screen.getByRole("row", { name: /Bunching/ });
    expect(bunching).toHaveTextContent("9.0%");
    expect(bunching).toHaveTextContent("8.0–10.0");
    expect(screen.getByRole("row", { name: /Excess wait/ })).toHaveTextContent("none");
    expect(screen.getByRole("row", { name: /Service delivered/ })).toHaveTextContent("D at best");
    expect(screen.getByRole("row", { name: /On-time rate/ })).toHaveTextContent("(shown, not graded)");
    expect(screen.getAllByText(/2026.11.20/).length).toBeGreaterThan(0);
  });

  it("shows the last three months, a month with no grade as N/A", () => {
    render(response());
    const trend = screen.getByLabelText("Last three months");
    expect(within(trend).getAllByRole("listitem")).toHaveLength(3);
    expect(within(trend).getByText("N/A")).toBeInTheDocument();
  });

  it("never shows a grade for a route the gate failed, only why", () => {
    render(response({ card: { ...card, grade: null, not_graded: "coverage_low", coverage_pct: 41, raise_to: [] } }));
    expect(screen.getByText("N/A: observation coverage 41.0%")).toBeInTheDocument();
    expect(screen.queryByText(/For the next grade:/)).not.toBeInTheDocument();
  });

  it("says when the route has no card that month", () => {
    render(response({ card: null }));
    expect(screen.getByText(/neither observed nor planned/)).toBeInTheDocument();
  });

  it("picks another closed month", async () => {
    const onMonth = render(response());
    await userEvent.selectOptions(screen.getByRole("combobox"), "2026-08");
    expect(onMonth).toHaveBeenCalledWith("2026-08");
  });

  it("prints the card alone, and lets the page print whole again afterwards", async () => {
    const print = vi.spyOn(window, "print").mockImplementation(() => {});
    render(response());
    await userEvent.click(screen.getByRole("button", { name: "Print card" }));
    expect(print).toHaveBeenCalled();
    expect(document.documentElement.dataset.printScope).toBe("report-card");
    window.dispatchEvent(new Event("afterprint"));
    expect(document.documentElement.dataset.printScope).toBeUndefined();
  });
});
