import { afterEach, describe, it, expect, vi } from "vitest";
import { screen, within } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { renderWithProviders } from "../test/renderWithProviders";
import { RoutesToCheckList } from "./RoutesToCheckList";
import { groupBySeverityBand } from "./routesToCheckBands";
import type { OverviewBandCounts, OverviewTopDelayedRoute } from "../api/types";
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

/** The server's uncapped per-band counts; by default, those of a list that
 *  holds every route in every band. */
function listedBands(rs: OverviewTopDelayedRoute[]): OverviewBandCounts {
  const listed = (band: string) => groupBySeverityBand(rs).find((g) => g.band === band)?.routes.length ?? 0;
  return {
    severe: { count: listed("severe"), threshold_min: 5 },
    moderate: { count: listed("moderate"), threshold_min: 3 },
    mild: { count: listed("mild"), threshold_min: 1.5 },
  };
}

type ListOptions = { routeCount?: number; windowFrom?: string; search?: string };

function list(rs: OverviewTopDelayedRoute[], bandCounts = listedBands(rs), options: ListOptions = {}) {
  const { routeCount = rs.length, windowFrom = "2026-09-24", search = "?from=2026-09-01&to=2026-09-30" } = options;
  return (
    <MemoryRouter initialEntries={[`/agencies/1/pulse${search}`]}>
      <Routes>
        <Route
          path="/agencies/:agencyId/pulse"
          element={
            <RoutesToCheckList
              routes={rs}
              bandCounts={bandCounts}
              routeCount={routeCount}
              minSamples={100}
              windowFrom={windowFrom}
              windowTo="2026-09-30"
              range="Sep 24 – Sep 30"
            />
          }
        />
      </Routes>
    </MemoryRouter>
  );
}

function renderList(rs: OverviewTopDelayedRoute[], bandCounts?: OverviewBandCounts, options?: ListOptions) {
  return renderWithProviders(list(rs, bandCounts, options));
}

describe("RoutesToCheckList", () => {
  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("heads each severity band with a sentence that counts its routes, worst band first, no empty bands", () => {
    renderList(routes());
    // The list counts the headline's window, not the whole period.
    expect(screen.getByText("Routes to check (last 7 days, Sep 24 – Sep 30)")).toBeInTheDocument();
    const headers = Array.from(document.querySelectorAll(".ov-check-band-hd")).map((h) => h.textContent);
    expect(headers).toEqual(["2 routes averaging 5 min or more late", "1 route averaging 1.5–3 min late"]);
  });

  // The list holds the worst five routes only, so any band can hold fewer
  // routes than the agency has in it: the last band to appear is cut off
  // where the five run out. Each header counts its band in full, and says
  // how many it lists only when it lists fewer.
  it("counts every route in a truncated band and says how many it lists, band by band", () => {
    const worst = [route("A", 9.1), route("B", 8.4), route("C", 6.2), route("D", 4.4), route("E", 3.1)];
    renderList(worst, {
      severe: { count: 3, threshold_min: 5 },
      moderate: { count: 14, threshold_min: 3 },
      mild: { count: 20, threshold_min: 1.5 },
    });
    const headers = Array.from(document.querySelectorAll(".ov-check-band-hd")).map((h) => h.textContent);
    // Mild has routes but none listed, so it gets no header at all.
    expect(headers).toEqual(["3 routes averaging 5 min or more late", "14 routes averaging 3–5 min late (top 2 shown)"]);
    expect(document.querySelectorAll(".ov-check-row")).toHaveLength(5);
  });

  it("marks a truncated top band the same way", () => {
    renderList([route("A", 9.1), route("B", 8.4), route("C", 7.7), route("D", 6.2), route("E", 5.3)], {
      severe: { count: 12, threshold_min: 5 },
      moderate: { count: 30, threshold_min: 3 },
      mild: { count: 40, threshold_min: 1.5 },
    });
    const headers = Array.from(document.querySelectorAll(".ov-check-band-hd")).map((h) => h.textContent);
    expect(headers).toEqual(["12 routes averaging 5 min or more late (top 5 shown)"]);
  });

  it("marks a truncated mild band the same way", () => {
    renderList([route("A", 6.2), route("B", 3.4), route("C", 2.9), route("D", 2.3), route("E", 1.8)], {
      severe: { count: 1, threshold_min: 5 },
      moderate: { count: 1, threshold_min: 3 },
      mild: { count: 9, threshold_min: 1.5 },
    });
    const headers = Array.from(document.querySelectorAll(".ov-check-band-hd")).map((h) => h.textContent);
    expect(headers).toEqual([
      "1 route averaging 5 min or more late",
      "1 route averaging 3–5 min late",
      "9 routes averaging 1.5–3 min late (top 3 shown)",
    ]);
  });

  it("gives each route's average its unit", () => {
    renderList(routes());
    expect(screen.getByText("6.6 min")).toBeInTheDocument();
  });

  it("opens the route's own page, keeping the period", () => {
    renderList(routes());
    const link = screen.getAllByRole("link")[0];
    const url = new URL(link.getAttribute("href")!, "http://x");
    expect(url.pathname).toBe("/agencies/1/routes/K31");
    expect(url.searchParams.get("from")).toBe("2026-09-01");
  });

  it("names each route by the shared label, keeping its route_id out of the row", () => {
    renderList([
      { route_code: "1471981638", route_short_name: "70-3", avg_min: 8.2 },
      { route_code: "K31", route_short_name: "観光通り線", avg_min: 6.6 },
      { route_code: "W53", route_short_name: null, avg_min: 5.1 },
    ]);
    const [hiroden, kanko, unnamed] = screen.getAllByRole("link");
    // A short code reads as a plate; the id stays only in the tooltip.
    expect(hiroden.querySelector(".route-label__plate")).toHaveTextContent("70-3");
    expect(hiroden).not.toHaveTextContent("1471981638");
    expect(hiroden.querySelector(".route-label")).toHaveAttribute("title", "1471981638");
    // A short name that is a name, not a code, reads as the line name.
    expect(kanko.querySelector(".route-label__plate")).toBeNull();
    expect(within(kanko).getByText("観光通り線")).toHaveClass("route-label__line");
    // W53 has no name -- it reads as "Route W53", with the code only once
    expect(unnamed).toHaveTextContent("Route W53");
    expect(within(unnamed).getAllByText(/W53/)).toHaveLength(1);
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
    expect((bars[0] as HTMLElement).style.getPropertyValue("--bar-share")).toBe("1");
  });

  it("keys every row for FLIP and sizes the bar with a transform, not a width", () => {
    renderList([route("3", 4.2), route("12", 2.1)]);
    const rows = screen.getAllByRole("link");
    expect(rows.map((r) => r.getAttribute("data-flip-key"))).toEqual(["3", "12"]);
    const fill = rows[0].querySelector<HTMLElement>(".ov-check-fill")!;
    expect(fill.style.getPropertyValue("--bar-share")).toBe("1");
    expect(fill.style.width).toBe("");
    expect(rows[1].querySelector<HTMLElement>(".ov-check-fill")!.style.getPropertyValue("--bar-share")).toBe("0.5");
  });

  it("keeps a route's row, and so its count-up and bar slide, when it crosses into another band", () => {
    const { rerender } = renderList([route("A", 6.0), route("B", 4.0)]);
    const before = screen.getByText("Route A").closest("a");
    rerender(list([route("A", 4.5), route("B", 4.0)]));
    expect(screen.getByText("Route A").closest("a")).toBe(before);
  });

  it("keys band headers for FLIP too, so a header slides with the rows around it", () => {
    renderList([route("A", 6.0), route("B", 4.0)]);
    const keys = Array.from(document.querySelectorAll("[data-flip-key]")).map((el) => el.getAttribute("data-flip-key"));
    expect(keys).toEqual(["band:severe", "A", "band:moderate", "B"]);
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

  it("says which routes it ranks: those observed often enough in its days", () => {
    renderList(routes(), undefined, { routeCount: 163 });
    expect(screen.getByText("Covers the 163 routes observed 100 or more times.")).toBeInTheDocument();
  });

  it("gives each row its change from the week before, signed, and none where there is nothing to compare", () => {
    renderList([
      { ...route("UP", 8.84), delta_min: 1.81 },
      { ...route("DOWN", 8.1), delta_min: -0.36 },
      { ...route("SAME", 6.0), delta_min: 0.02 },
      { ...route("NEW", 5.5), delta_min: null },
    ]);
    const changes = screen.getAllByRole("link").map((row) => row.querySelector(".ov-check-change")?.textContent ?? null);
    expect(changes).toEqual([
      "+1.8 min vs last week",
      "\u22120.4 min vs last week",
      "0.0 min vs last week",
      null,
    ]);
  });

  it("compares a shorter window with the same number of days before it", () => {
    renderList([{ ...route("UP", 8.84), delta_min: 1.81 }], undefined, { windowFrom: "2026-09-28" });
    expect(screen.getByText("+1.8 min vs the 3 days before")).toBeInTheDocument();
    expect(screen.getByText("Routes to check (last 3 days, Sep 24 – Sep 30)")).toBeInTheDocument();
  });

  it("links to every route it ranks, as the Routes table over its own days, worst average first", () => {
    renderList(routes(), undefined, {
      routeCount: 163,
      search: "?from=2026-09-01&to=2026-09-30&dow=weekday&sparse=1&sort=on_time&order=best&report=on_time",
    });
    const more = screen.getByRole("link", { name: "See all 163 routes →" });
    const url = new URL(more.getAttribute("href")!, "http://x");
    expect(url.pathname).toBe("/agencies/1/routes");
    expect(url.searchParams.get("from")).toBe("2026-09-24");
    expect(url.searchParams.get("to")).toBe("2026-09-30");
    // Sorted by average delay, worst first (the table's default order), at
    // the table's own floor, the list's; the rest of the scope holds.
    expect(url.searchParams.get("sort")).toBe("avg");
    expect(url.searchParams.has("order")).toBe(false);
    expect(url.searchParams.has("report")).toBe(false);
    expect(url.searchParams.has("sparse")).toBe(false);
    expect(url.searchParams.get("dow")).toBe("weekday");
  });

  it("offers no link when it already lists every route it ranks", () => {
    renderList(routes(), undefined, { routeCount: 3 });
    expect(screen.queryByRole("link", { name: /See all/ })).toBeNull();
  });
});
