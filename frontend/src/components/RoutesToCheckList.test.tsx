import { describe, it, expect, vi } from "vitest";
import { screen, fireEvent } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { renderWithProviders } from "../test/renderWithProviders";
import { RoutesToCheckList } from "./RoutesToCheckList";
import * as rangeContext from "../api/rangeContext";
import type { OverviewTopDelayedRoute } from "../api/types";

function routes(): OverviewTopDelayedRoute[] {
  return [
    { route_code: "K31", route_short_name: "観光通り線", avg_min: 6.6 },
    { route_code: "K37", route_short_name: "観光通り線", avg_min: 5.7 },
    { route_code: "W53", route_short_name: null, avg_min: 2.1 },
  ];
}

// RoutesToCheckList calls useRangeContext (react-router-dom's useSearchParams
// under the hood), so — matching the existing pattern in
// RouteForecastSection.test.tsx — every render needs a <MemoryRouter>.
function renderList(rs: OverviewTopDelayedRoute[]) {
  return renderWithProviders(
    <MemoryRouter>
      <RoutesToCheckList routes={rs} />
    </MemoryRouter>,
  );
}

describe("RoutesToCheckList", () => {
  it("renders one row per route with a separate code column, name, and absolute delay", () => {
    renderList(routes());
    expect(screen.getByText("Routes to check now")).toBeInTheDocument();
    expect(screen.getByText("K31")).toBeInTheDocument();
    expect(screen.getAllByText("観光通り線")).toHaveLength(2);
    expect(screen.getByText("6.6")).toBeInTheDocument();
    // W53 has no route_short_name — the name column falls back to the code,
    // so "W53" appears twice (once in the code column, once in the name column).
    expect(screen.getAllByText("W53")).toHaveLength(2);
  });

  it("scales each bar relative to the list's own max avg_min", () => {
    renderList(routes());
    const bars = document.querySelectorAll(".ov-check-fill");
    expect(bars).toHaveLength(3);
    expect((bars[0] as HTMLElement).style.width).toBe("100%");
    const w53Width = parseFloat((bars[2] as HTMLElement).style.width);
    expect(w53Width).toBeCloseTo((2.1 / 6.6) * 100, 0);
  });

  it("shows the empty-state message when there are no routes", () => {
    renderList([]);
    expect(screen.getByText("No routes need attention")).toBeInTheDocument();
  });

  it("narrows the shared route filter to the clicked route", () => {
    const update = vi.fn();
    vi.spyOn(rangeContext, "useRangeContext").mockReturnValue([
      { from: "2026-06-01", to: "2026-06-07", dow: "all", time_band: "all", service: "all", routes: [] },
      update,
    ]);
    renderList(routes());
    fireEvent.click(screen.getByText("K31"));
    expect(update).toHaveBeenCalledWith({ routes: ["K31"] });
  });
});
