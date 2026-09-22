import { describe, it, expect, vi, afterEach } from "vitest";
import { screen } from "@testing-library/react";
import { MemoryRouter, Routes, Route } from "react-router-dom";
import { renderWithProviders } from "../test/renderWithProviders";
import * as hooks from "../api/hooks";
import * as useRouteNamesModule from "../api/useRouteNames";
import { RouteAnalysisTab } from "./RouteAnalysisTab";
import type { RouteShapeResponse } from "../api/types";

function mockSupportHooks() {
  vi.spyOn(hooks, "useRoutes").mockReturnValue({ data: [], isLoading: false } as never);
  vi.spyOn(useRouteNamesModule, "useRouteNames").mockReturnValue({
    data: new Map(),
    isLoading: false,
    format: (code: string | null | undefined) => code ?? "—",
  });
}

function shape(stops: RouteShapeResponse["stops"] = []): RouteShapeResponse {
  return { route: "R1", geometry: null, stops };
}

function renderTab(initialPath: string) {
  renderWithProviders(
    <MemoryRouter initialEntries={[initialPath]}>
      <Routes>
        <Route path="/agencies/:agencyId/route-analysis" element={<RouteAnalysisTab />} />
      </Routes>
    </MemoryRouter>,
  );
}

describe("RouteAnalysisTab", () => {
  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("prompts to choose a route and pattern when no single route is selected", () => {
    mockSupportHooks();
    vi.spyOn(hooks, "useRouteShape").mockReturnValue({ data: undefined, isPending: false, error: null, refetch: vi.fn() } as never);
    renderTab("/agencies/1/route-analysis");
    expect(screen.getByText("Choose a route and service pattern")).toBeInTheDocument();
  });

  it("shows the empty state when the selected route has no stop observations", () => {
    mockSupportHooks();
    vi.spyOn(hooks, "useRouteShape").mockReturnValue({ data: shape([]), isPending: false, error: null, refetch: vi.fn() } as never);
    renderTab("/agencies/1/route-analysis?routes=R1");
    expect(screen.getByText("No observations match these filters")).toBeInTheDocument();
  });

  it("renders the investigate heading and stop-delay content once a route has data", () => {
    mockSupportHooks();
    vi.spyOn(hooks, "useRouteShape").mockReturnValue({
      data: shape([
        { stop_sequence: 1, stop_name: "Stop A", lon: 140.7, lat: 40.8, avg_min: 2.4, samples: 10 },
      ]),
      isPending: false,
      error: null,
      refetch: vi.fn(),
    } as never);
    renderTab("/agencies/1/route-analysis?routes=R1");
    expect(screen.getByRole("heading", { name: "Where does delay build up?" })).toBeInTheDocument();
    expect(screen.getByText("Delay by stop")).toBeInTheDocument();
  });
});

function renderRecoveryTab(
  path: string,
  response: RouteShapeResponse | undefined = { route: "A05", geometry: null, stops: [] } as RouteShapeResponse,
) {
  vi.spyOn(hooks, "useRouteShape").mockReturnValue({
    data: response,
    isPending: false,
    error: null,
    refetch: vi.fn(),
  } as never);
  vi.spyOn(hooks, "useRoutes").mockReturnValue({ data: [], isLoading: false } as never);
  vi.spyOn(hooks, "useAgencies").mockReturnValue({
    data: [{ agency_id: 8, agency_name: "A", feed_url: "", static_url: null, latest_data_date: "2026-05-01" }],
    isPending: false,
  } as never);
  renderWithProviders(
    <MemoryRouter initialEntries={[path]}>
      <Routes>
        <Route path="/agencies/:agencyId/route-analysis" element={<RouteAnalysisTab />} />
      </Routes>
    </MemoryRouter>,
  );
}

describe("RouteAnalysisTab empty state recoveries", () => {
  it("offers a jump-to-latest-data recovery when a route is scoped but its window has no stop data", () => {
    renderRecoveryTab("/agencies/8/route-analysis?from=2020-01-01&to=2020-01-07&routes=A05");
    expect(screen.getByRole("button", { name: "Jump to the latest data" })).toBeInTheDocument();
  });

  it("offers a reset-service recovery when service is scoped to a non-default value", () => {
    renderRecoveryTab("/agencies/8/route-analysis?from=2020-01-01&to=2020-01-07&routes=A05&service=%E5%B9%B3%E6%97%A5");
    expect(screen.getByRole("button", { name: "Reset service type to all" })).toBeInTheDocument();
  });
});
