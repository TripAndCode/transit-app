import { describe, it, expect } from "vitest";
import { screen } from "@testing-library/react";
import { renderWithProviders } from "../test/renderWithProviders";
import { RoutesToCheckList } from "./RoutesToCheckList";
import type { OverviewTopDelayedRoute } from "../api/types";

function routes(): OverviewTopDelayedRoute[] {
  return [
    { route_code: "K31", route_short_name: "観光通り線", avg_min: 6.6 },
    { route_code: "K37", route_short_name: "観光通り線", avg_min: 5.7 },
    { route_code: "W53", route_short_name: null, avg_min: 2.1 },
  ];
}

describe("RoutesToCheckList", () => {
  it("renders one row per route with code, name, and absolute delay", () => {
    renderWithProviders(<RoutesToCheckList routes={routes()} />);
    // renderWithProviders forces the "en" locale (see its own doc comment),
    // so assert against the English copy, matching the convention used by
    // OverviewHeroRow.test.tsx.
    expect(screen.getByText("Routes to check now")).toBeInTheDocument();
    expect(screen.getByText(/観光通り線 \(K31\)/)).toBeInTheDocument();
    expect(screen.getByText(/6\.6/)).toBeInTheDocument();
    // W53 has no route_short_name — falls back to showing the code alone,
    // not blank.
    expect(screen.getByText("W53")).toBeInTheDocument();
  });

  it("scales each bar relative to the list's own max avg_min", () => {
    renderWithProviders(<RoutesToCheckList routes={routes()} />);
    const bars = document.querySelectorAll(".ov-pareto-fill");
    expect(bars).toHaveLength(3);
    // K31 (6.6, the max) should be 100% width; W53 (2.1) proportionally less.
    expect((bars[0] as HTMLElement).style.width).toBe("100%");
    const w53Width = parseFloat((bars[2] as HTMLElement).style.width);
    expect(w53Width).toBeCloseTo((2.1 / 6.6) * 100, 0);
  });

  it("shows the empty-state message when there are no routes", () => {
    renderWithProviders(<RoutesToCheckList routes={[]} />);
    expect(screen.getByText("No routes need attention")).toBeInTheDocument();
  });
});
