import { describe, it, expect, vi, beforeEach } from "vitest";
import { screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Routes, Route } from "react-router-dom";
import { renderWithProviders } from "../test/renderWithProviders";
import { LiveTab } from "./LiveTab";
import * as hooks from "../api/hooks";
import type { RouteSummary } from "../api/types";

vi.mock("../api/useRouteNames", () => ({
  useRouteNames: () => ({ data: {}, isLoading: false, format: (rc: string) => rc }),
}));

function route(over: Partial<RouteSummary>): RouteSummary {
  return {
    route_code: "R1", service_type: "weekday", avg_delay_sec: 300, worst_delay_sec: 600,
    trips_observed: 5, samples: 50, last_seen_at: null, baseline_avg_sec: 120,
    baseline_p90_sec: 360, deviation_sec: 180, bucket: "anomaly",
    low_confidence: false, has_baseline: true, ...over,
  };
}

function renderTab() {
  renderWithProviders(
    <MemoryRouter initialEntries={["/1"]}>
      <Routes>
        <Route path="/:agencyId" element={<LiveTab />} />
      </Routes>
    </MemoryRouter>,
  );
}

beforeEach(() => {
  vi.spyOn(hooks, "useRouteTrips").mockReturnValue({ data: { date: "2026-06-09", trips: [] }, isLoading: false } as never);
  vi.spyOn(hooks, "useRouteStopProfile").mockReturnValue({ data: { date: "2026-06-09", stops: [] }, isLoading: false } as never);
});

describe("LiveTab", () => {
  it("renders bucket headings with counts and routes under them", () => {
    vi.spyOn(hooks, "useTodayRouteSummary").mockReturnValue({
      data: {
        latest_captured_at: "2026-06-09T10:00:00Z", date: "2026-06-09",
        routes: [route({ route_code: "BAD", bucket: "anomaly", deviation_sec: 240 }),
                 route({ route_code: "OK", bucket: "normal", deviation_sec: 0 })],
      },
      isLoading: false, error: null, refetch: vi.fn(), dataUpdatedAt: Date.now(), isFetching: false,
    } as never);
    renderTab();
    expect(screen.getByText(/Anomalous/)).toBeInTheDocument();
    expect(screen.getByText("BAD")).toBeInTheDocument();
  });

  it("opens the drilldown panel when a route row is clicked", async () => {
    vi.spyOn(hooks, "useTodayRouteSummary").mockReturnValue({
      data: { latest_captured_at: "2026-06-09T10:00:00Z", date: "2026-06-09", routes: [route({ route_code: "BAD" })] },
      isLoading: false, error: null, refetch: vi.fn(), dataUpdatedAt: Date.now(), isFetching: false,
    } as never);
    renderTab();
    await userEvent.click(screen.getByRole("button", { name: /BAD/ }));
    expect(screen.getByText(/detail/)).toBeInTheDocument();
  });
});
