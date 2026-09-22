import { describe, it, expect, vi } from "vitest";
import { fireEvent, screen } from "@testing-library/react";
import { MemoryRouter, Routes, Route, useSearchParams } from "react-router-dom";
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

function SearchProbe() {
  const [params] = useSearchParams();
  return <span data-testid="search">{params.toString()}</span>;
}

function renderOverview(data: OverviewSummary) {
  vi.spyOn(hooks, "useOverviewSummary").mockReturnValue({ data, isPending: false, error: null, refetch: vi.fn() } as never);
  vi.spyOn(hooks, "usePeakHourBreakdown").mockReturnValue({ data: null, isLoading: false } as never);
  renderWithProviders(
    <MemoryRouter initialEntries={["/agencies/8/overview?from=2030-01-01&to=2030-01-07"]}>
      <Routes>
        <Route path="/agencies/:agencyId/overview" element={<><OverviewTab /><SearchProbe /></>} />
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

  it("does not render a mini-map — removed as low-value decorative content, spatial exploration stays on the dedicated Map tab", () => {
    renderOverview(
      summary({
        headline: { avg_min: 3.2, baseline_avg_min: 2.8, delta_min: 0.4, delta_pct: 14.3, samples: 50, window_from: "2026-06-01", window_to: "2026-06-07" },
        top_delayed: { routes: [{ route_code: "R1", route_short_name: "Line 1", avg_min: 6.0 }], delayed_count: 1 },
      }),
    );
    expect(screen.getByText("Routes to check now")).toBeInTheDocument();
    // Asserted on the map container MapLibre itself creates, not on a class
    // name this screen once used: `.ov-map-strip` exists in no stylesheet and
    // no component, so asserting its absence could never fail and guarded
    // nothing. Any map reintroduced here would mount through the app's own
    // MapLibre helper and carry this class whatever the wrapper is called.
    expect(document.querySelector(".maplibregl-map")).not.toBeInTheDocument();
    expect(document.querySelector("canvas")).not.toBeInTheDocument();
  });

  // Selecting an hour writes two query keys. Written as two per-key setters
  // they would not compose -- the second navigation starts from the same
  // params snapshot as the first and drops it -- so the hour would never
  // reach the URL and the breakdown would never open.
  it("writes the peak hour to the URL when an hour is picked", () => {
    renderOverview(
      summary({
        headline: { avg_min: 3.2, baseline_avg_min: 2.8, delta_min: 0.4, delta_pct: 14.3, samples: 50, window_from: "2026-06-01", window_to: "2026-06-07" },
        peak_hour: { by_hour: Array.from({ length: 24 }, (_, i) => i / 10), peak_hour: 8, peak_avg_min: 2.3 },
      }),
    );
    // jsdom reports a zero-size rect, and the ribbon maps a click to an hour
    // through its own width, so the geometry has to be supplied here.
    const ribbon = screen.getByRole("img", { name: "Worst hour of day" });
    ribbon.getBoundingClientRect = () =>
      ({ width: 660, height: 90, left: 0, top: 0, right: 660, bottom: 90, x: 0, y: 0, toJSON: () => ({}) }) as DOMRect;
    fireEvent.click(ribbon, { clientX: 8 * (660 / 24) + 1 });
    const search = new URLSearchParams(screen.getByTestId("search").textContent ?? "");
    expect(search.has("peak_hour")).toBe(true);
    // The range the tab was opened with must survive the selection write.
    expect(search.get("from")).toBe("2030-01-01");
  });

  it("renders peak-hour, concentration, and service-split content inline, with nothing to disclose", () => {
    renderOverview(
      summary({
        headline: { avg_min: 3.2, baseline_avg_min: 2.8, delta_min: 0.4, delta_pct: 14.3, samples: 50, window_from: "2026-06-01", window_to: "2026-06-07" },
        concentration: { top_routes: [{ route_code: "R1", route_short_name: "Line 1", share_pct: 60 }], rest_share_pct: 40 },
        peak_hour: { by_hour: Array(24).fill(1), peak_hour: 17, peak_avg_min: 4.8 },
        service_split: { "平日": 3.1, "土日祝": 2.0 },
      }),
    );
    expect(screen.getByText("Delay concentration")).toBeInTheDocument();
    expect(screen.getByText("Worst hour of day")).toBeInTheDocument();
    expect(screen.getByText("By service day")).toBeInTheDocument();
    // Progressively revealed, not gated behind a disclosure widget.
    expect(document.querySelector("details")).not.toBeInTheDocument();
    expect(document.querySelector("summary")).not.toBeInTheDocument();
  });
});
