import { afterEach, describe, it, expect, vi } from "vitest";
import { screen } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { renderWithProviders } from "../test/renderWithProviders";
import { RoutesToCheckList } from "./RoutesToCheckList";
import type { OverviewTopDelayedRoute } from "../api/types";
import { stubReducedMotion } from "../test/reducedMotion";
import * as flipModule from "../hooks/useFlipRows";

function routes(): OverviewTopDelayedRoute[] {
  return [
    { route_code: "K31", route_short_name: "観光通り線", avg_min: 6.6 },
    { route_code: "K37", route_short_name: "観光通り線", avg_min: 5.7 },
    { route_code: "W53", route_short_name: null, avg_min: 2.1 },
  ];
}

function route(code: string, avgMin: number): OverviewTopDelayedRoute {
  return { route_code: code, route_short_name: null, avg_min: avgMin };
}

function list(rs: OverviewTopDelayedRoute[]) {
  return (
    <MemoryRouter initialEntries={["/agencies/1/pulse?from=2026-09-01&to=2026-09-30"]}>
      <Routes>
        <Route path="/agencies/:agencyId/pulse" element={<RoutesToCheckList routes={rs} />} />
      </Routes>
    </MemoryRouter>
  );
}

function renderList(rs: OverviewTopDelayedRoute[]) {
  return renderWithProviders(list(rs));
}

describe("RoutesToCheckList", () => {
  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("heads each severity band with a sentence that counts its routes, worst band first, no empty bands", () => {
    renderList(routes());
    expect(screen.getByText("Routes to check now")).toBeInTheDocument();
    const headers = Array.from(document.querySelectorAll(".ov-check-band-hd")).map((h) => h.textContent);
    expect(headers).toEqual(["2 routes averaging more than 5 min late", "1 route averaging 1.5–3 min late"]);
  });

  it("gives each route's average its unit", () => {
    renderList(routes());
    expect(screen.getByText("6.6 min")).toBeInTheDocument();
  });

  it("opens the route's own page, keeping the period", () => {
    renderList(routes());
    const link = screen.getByText("K31").closest("a")!;
    const url = new URL(link.getAttribute("href")!, "http://x");
    expect(url.pathname).toBe("/agencies/1/routes/K31");
    expect(url.searchParams.get("from")).toBe("2026-09-01");
  });

  it("shows the route's name with its code de-emphasized, not as a separate raw-code column", () => {
    renderList(routes());
    // K31 and K37 share the same short_name -- both rows render it
    expect(screen.getAllByText("観光通り線")).toHaveLength(2);
    expect(screen.getByText("K31")).toHaveClass("route-label__code");
    expect(screen.getByText("K37")).toHaveClass("route-label__code");
    // W53 has no name -- it reads as "Route W53", with the code only once
    expect(screen.getAllByText(/W53/)).toHaveLength(1);
    expect(screen.getByText("Route W53")).toBeInTheDocument();
  });

  it("falls back to the code when route_short_name is an empty string, not just null", () => {
    // Real backend data can return "" (not null) for an unnamed route --
    // `??` doesn't catch that, only `||` does, so an empty name must still
    // fall back to the code rather than render a blank row.
    renderList([{ route_code: "R99", route_short_name: "", avg_min: 4.0 }]);
    expect(screen.getByText("Route R99")).toBeInTheDocument();
  });

  it("scales each bar relative to the list's own max avg_min", () => {
    renderList(routes());
    const bars = document.querySelectorAll(".ov-check-fill");
    expect(bars).toHaveLength(3);
    expect((bars[0] as HTMLElement).style.getPropertyValue("--check-share")).toBe("1");
  });

  it("keys every row for FLIP and sizes the bar with a transform, not a width", () => {
    renderList([route("3", 4.2), route("12", 2.1)]);
    const rows = screen.getAllByRole("link");
    expect(rows.map((r) => r.getAttribute("data-flip-key"))).toEqual(["3", "12"]);
    const fill = rows[0].querySelector<HTMLElement>(".ov-check-fill")!;
    expect(fill.style.getPropertyValue("--check-share")).toBe("1");
    expect(fill.style.width).toBe("");
    expect(rows[1].querySelector<HTMLElement>(".ov-check-fill")!.style.getPropertyValue("--check-share")).toBe("0.5");
  });

  it("keeps a route's row, and so its count-up and bar slide, when it crosses into another band", () => {
    const { rerender } = renderList([route("A", 6.0), route("B", 4.0)]);
    const before = screen.getByText("Route A").closest("a");
    rerender(list([route("A", 4.5), route("B", 4.0)]));
    expect(screen.getByText("Route A").closest("a")).toBe(before);
  });

  it("re-measures for FLIP when a band header appears or goes, even if the route order holds", () => {
    const flip = vi.spyOn(flipModule, "useFlipRows");
    const { rerender } = renderList([route("A", 6.0), route("B", 4.0)]);
    const first = flip.mock.calls.at(-1)?.[1];
    rerender(list([route("A", 4.5), route("B", 4.0)]));
    expect(flip.mock.calls.at(-1)?.[1]).not.toBe(first);
  });

  it("prints the figure with the numeric face and travels on change (reduced motion prints)", () => {
    stubReducedMotion();
    const { rerender } = renderList([route("3", 4.2)]);
    expect(screen.getByText("4.2 min")).toHaveClass("num");
    rerender(list([route("3", 3.0)]));
    expect(screen.getByText("3.0 min")).toBeInTheDocument();
  });

  it("shows the empty-state message when there are no routes", () => {
    renderList([]);
    expect(screen.getByText("No routes need attention")).toBeInTheDocument();
  });

  it("shows the empty-state message when routes is non-empty but every route is in the excluded ok band", () => {
    // The backend's worst-N query has no minimum-delay floor, so a healthy
    // agency's routes prop can be non-empty while every route is <1.5min
    // (dropped by groupBySeverityBand). Gating the empty-state on
    // routes.length instead of groups.length would render the "Routes to
    // check now" header over blank space here -- regression test for that.
    renderList([{ route_code: "A", route_short_name: null, avg_min: 0.5 }]);
    expect(screen.getByText("No routes need attention")).toBeInTheDocument();
    expect(document.querySelector(".ov-check-row")).not.toBeInTheDocument();
  });
});
