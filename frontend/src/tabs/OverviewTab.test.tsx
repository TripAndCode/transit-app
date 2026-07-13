import { describe, it, expect, vi } from "vitest";
import { screen } from "@testing-library/react";
import { MemoryRouter, Routes, Route } from "react-router-dom";
import { renderWithProviders } from "../test/renderWithProviders";
import { OverviewTab } from "./OverviewTab";
import * as hooks from "../api/hooks";
import type { OverviewSummary } from "../api/types";

function summary(partial: Partial<OverviewSummary> = {}): OverviewSummary {
  return {
    headline: { avg_min: null, baseline_avg_min: null, delta_min: null, delta_pct: null, samples: 0, window_from: "2030-01-01", window_to: "2030-01-07" },
    movers: { worse: [], better: [] },
    concentration: { top_routes: [], rest_share_pct: 0 },
    top_delayed: { routes: [], delayed_count: 0 },
    peak_hour: null,
    service_split: {},
    sparkline_points: [],
    ...partial,
  };
}

function renderOverview(data: OverviewSummary) {
  vi.spyOn(hooks, "useOverviewSummary").mockReturnValue({ data, isPending: false, error: null, refetch: vi.fn() } as never);
  vi.spyOn(hooks, "usePeakHourBreakdown").mockReturnValue({ data: null, isLoading: false } as never);
  renderWithProviders(
    <MemoryRouter initialEntries={["/agencies/8/overview?from=2030-01-01&to=2030-01-07"]}>
      <Routes>
        <Route path="/agencies/:agencyId/overview" element={<OverviewTab />} />
      </Routes>
    </MemoryRouter>,
  );
}

describe("OverviewTab", () => {
  it("shows the empty state when every real signal is empty, even though peak_hour is non-null", () => {
    // peak_hour reads a fixed analyze-period rollup with no date column
    // (see pipeline/reports/overview.py's _peak_hour docstring) — it stays
    // non-null for any date range once an agency has ever had data, so it
    // must NOT count as evidence that THIS range has data.
    renderOverview(
      summary({
        peak_hour: { by_hour: [null, null, 1.2, 2.3], peak_hour: 3, peak_avg_min: 2.3 },
      }),
    );
    expect(screen.getByText("No observations in this range. Try a wider window.")).toBeInTheDocument();
  });

  it("shows real content when the headline has samples", () => {
    renderOverview(summary({ headline: { avg_min: 3.2, baseline_avg_min: 2.8, delta_min: 0.4, delta_pct: 14.3, samples: 50, window_from: "2026-06-01", window_to: "2026-06-07" } }));
    expect(screen.queryByText("No observations in this range. Try a wider window.")).not.toBeInTheDocument();
  });

  it("renders the routes-to-check list before the map (actionable content first)", () => {
    renderOverview(
      summary({
        headline: { avg_min: 3.2, baseline_avg_min: 2.8, delta_min: 0.4, delta_pct: 14.3, samples: 50, window_from: "2026-06-01", window_to: "2026-06-07" },
        top_delayed: { routes: [{ route_code: "R1", route_short_name: "Line 1", avg_min: 6.0 }], delayed_count: 1 },
      }),
    );
    const routesHeading = screen.getByText("Routes to check now");
    const mapPlaceholder = document.querySelector(".skeleton, .ov-map-strip")!;
    expect(mapPlaceholder).toBeTruthy();
    // DOCUMENT_POSITION_FOLLOWING means mapPlaceholder comes AFTER routesHeading
    expect(routesHeading.compareDocumentPosition(mapPlaceholder) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
  });
});
