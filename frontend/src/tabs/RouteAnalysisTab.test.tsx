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
