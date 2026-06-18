import { describe, it, expect, vi } from "vitest";
import { screen } from "@testing-library/react";
import { MemoryRouter, Routes, Route } from "react-router-dom";
import { renderWithProviders } from "../test/renderWithProviders";
import { ForecastTab } from "./ForecastTab";
import * as hooks from "../api/hooks";
import type { Route as RouteType } from "../api/types";

function route(code: string): RouteType {
  return {
    route_id: code,
    route_short_name: code,
    route_long_name: null,
    route_code: code,
    trip_headsigns: [],
  };
}

function profile(over = {}) {
  const hours = Array.from({ length: 24 }, (_, h) => ({
    hour: h,
    expected_avg_min: h === 8 ? 12 : h === 9 ? 3 : null,
    samples: h === 8 ? 500 : h === 9 ? 10 : 0,
    low_confidence: h === 9,
  }));
  return { route: "R1", service_type: "平日", hours, disclaimer: "test disclaimer", ...over };
}

function renderTab(routes: RouteType[], services: string[] = ["平日"]) {
  vi.spyOn(hooks, "useRoutes").mockReturnValue({
    data: routes,
    isPending: false,
    isLoading: false,
    refetch: vi.fn(),
  } as never);
  vi.spyOn(hooks, "useForecastServices").mockReturnValue({
    data: { service_types: services },
    isPending: false,
    error: null,
    refetch: vi.fn(),
  } as never);
  renderWithProviders(
    <MemoryRouter initialEntries={["/agencies/1/forecast"]}>
      <Routes>
        <Route path="/agencies/:agencyId/forecast" element={<ForecastTab />} />
      </Routes>
    </MemoryRouter>,
  );
}

describe("ForecastTab", () => {
  it("prompts to pick a route when no routes are available", () => {
    vi.spyOn(hooks, "useForecastProfile").mockReturnValue({
      data: undefined,
      isPending: false,
      error: null,
      refetch: vi.fn(),
    } as never);
    renderTab([]);
    expect(screen.getByText(/pick a route/i)).toBeInTheDocument();
  });

  it("renders hourly bars + low-confidence marker + disclaimer when data is present", () => {
    vi.spyOn(hooks, "useForecastProfile").mockReturnValue({
      data: profile(),
      isPending: false,
      error: null,
      refetch: vi.fn(),
    } as never);
    renderTab([route("R1")]);
    expect(screen.getAllByTestId("forecast-bar").length).toBeGreaterThanOrEqual(2);
    expect(screen.getByTestId("forecast-bar-lowconf")).toBeInTheDocument();
    expect(screen.getByText("test disclaimer")).toBeInTheDocument();
  });

  it("shows a no-data note when every hour is null", () => {
    const empty = profile({
      hours: Array.from({ length: 24 }, (_, h) => ({
        hour: h,
        expected_avg_min: null,
        samples: 0,
        low_confidence: false,
      })),
    });
    vi.spyOn(hooks, "useForecastProfile").mockReturnValue({
      data: empty,
      isPending: false,
      error: null,
      refetch: vi.fn(),
    } as never);
    renderTab([route("R1")]);
    expect(screen.getByText(/no delay measurements/i)).toBeInTheDocument();
  });
});
