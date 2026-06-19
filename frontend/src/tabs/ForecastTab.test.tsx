import { describe, it, expect, vi } from "vitest";
import { screen, fireEvent, within } from "@testing-library/react";
import { MemoryRouter, Routes, Route } from "react-router-dom";
import { renderWithProviders } from "../test/renderWithProviders";
import { ForecastTab } from "./ForecastTab";
import * as hooks from "../api/hooks";
import type { Route as RouteType } from "../api/types";

function route(code: string): RouteType {
  return { route_id: code, route_short_name: code, route_long_name: null, route_code: code, trip_headsigns: [] };
}

function heatmap() {
  const cells = [];
  for (let d = 1; d <= 7; d++) {
    for (let h = 0; h < 24; h++) {
      let v: number | null = null;
      let n = 0;
      let low = false;
      if (h === 8) {
        v = 5 + d;
        n = 300;
        if (d === 1) {
          n = 10;
          low = true;
        }
      }
      cells.push({ dow: d, hour: h, expected_avg_min: v, samples: n, low_confidence: low });
    }
  }
  return { route: "R1", cells, disclaimer: "test disclaimer" };
}

function renderTab(routes: RouteType[], data: unknown = heatmap()) {
  vi.spyOn(hooks, "useRoutes").mockReturnValue({ data: routes, isPending: false, isLoading: false, refetch: vi.fn() } as never);
  vi.spyOn(hooks, "useForecastHeatmap").mockReturnValue({ data, isPending: false, error: null, refetch: vi.fn() } as never);
  renderWithProviders(
    <MemoryRouter initialEntries={["/agencies/1/forecast"]}>
      <Routes>
        <Route path="/agencies/:agencyId/forecast" element={<ForecastTab />} />
      </Routes>
    </MemoryRouter>,
  );
}

describe("ForecastTab", () => {
  it("prompts to pick a route when none are available", () => {
    renderTab([], undefined);
    expect(screen.getByText(/pick a route/i)).toBeInTheDocument();
  });

  it("renders the three cards with heatmap + margins, no modal until clicked", () => {
    renderTab([route("R1")]);
    expect(screen.getByText("By day & hour")).toBeInTheDocument();
    expect(screen.getAllByTestId("hm-cell").length).toBe(7); // compact card, hour-8 only
    expect(screen.getByTestId("hm-cell-lowconf")).toBeInTheDocument();
    expect(screen.getAllByTestId("dow-bar").length).toBe(7);
    expect(screen.getAllByTestId("hr-bar").length).toBe(1);
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });

  it("opens the heatmap detail modal (stats + disclaimer) when the card is clicked", () => {
    renderTab([route("R1")]);
    fireEvent.click(screen.getByTestId("fc-card-hm"));
    const dialog = screen.getByRole("dialog");
    expect(within(dialog).getByText("Worst")).toBeInTheDocument(); // stat label
    expect(within(dialog).getByText("test disclaimer")).toBeInTheDocument();
  });
});
