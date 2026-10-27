import { afterEach, describe, expect, it, vi } from "vitest";
import { screen, within } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { renderWithProviders } from "../../test/renderWithProviders";
import * as hooks from "../../api/hooks";
import { SCOPE_EXTRAS_NONE, type Scope } from "../../api/scope";
import { RouteSummaryStrip } from "./RouteSummaryStrip";

const CTX: Scope = {
  ...SCOPE_EXTRAS_NONE, from: "2026-09-24", to: "2026-10-23", dow: "all", time_band: "all", service: "all", routes: ["R"],
};
const DEFINITION = {
  preset: null, early_tolerance_sec: null, late_tolerance_sec: 60, exclusion_threshold_sec: 7200,
  measurement_point: "all_stops_all_observations", dedup_rule: "latest_observation_per_stop_event",
};

function reports(byType: Record<string, unknown[]>, rowsTotal = 174) {
  return vi.spyOn(hooks, "useReport").mockImplementation((_id, type) => ({
    data: { report_type: type, rows: byType[type ?? ""] ?? [], rows_total: rowsTotal, definition: DEFINITION },
    isPending: false,
  }) as never);
}

function overview(topRoutes: { route_code: string; share_pct: number }[]) {
  vi.spyOn(hooks, "useOverviewSummary").mockReturnValue({
    data: { concentration: { top_routes: topRoutes.map((r) => ({ ...r, route_short_name: null })), rest_share_pct: 0 } },
    isPending: false,
  } as never);
}

function render() {
  renderWithProviders(<MemoryRouter><RouteSummaryStrip agencyId={8} route="R" ctx={CTX} /></MemoryRouter>);
  return screen.getByRole("region", { name: "This route at a glance" });
}

describe("RouteSummaryStrip", () => {
  afterEach(() => vi.restoreAllMocks());

  it("states the route's figures as the agency's route lists do", () => {
    const useReport = reports({
      ranking: [["A", null, "5.1", "4", "9", 500], ["R", null, "3.06", "2", "8", 143516]],
      on_time: [["R", null, "49.0", "3.06", 143516, false, 0.3]],
      worst_5min: [["R", null, 26192, "3.06", 143516]],
    });
    overview([{ route_code: "R", share_pct: 5 }]);
    const strip = render();
    expect(strip).toHaveTextContent("Mean delay3.1 minNo. 2 of 174 routes, most delayed first");
    expect(strip).toHaveTextContent("On time49%");
    expect(strip).toHaveTextContent("Over 5 min late18%");
    expect(strip).toHaveTextContent("Share of all lateness5.0%No. 1 in the agency");
    expect(strip).toHaveTextContent("On time = no more than 1 min late (early departures count as on time).");
    // A rank is a place among every route, so the lists are the agency's own.
    for (const call of useReport.mock.calls) expect(call[2].routes).toEqual([]);
  });

  it("links to this route's time-of-day and why pages", () => {
    reports({});
    overview([]);
    const strip = render();
    expect(within(strip).getByRole("link", { name: "This route by time of day →" })).toHaveAttribute(
      "href", expect.stringMatching(/^\/agencies\/8\/time\?.*report=trend/),
    );
    expect(within(strip).getByRole("link", { name: "This route by time of day →" }).getAttribute("href")).toContain("routes=R");
    expect(within(strip).getByRole("link", { name: "Why this route runs late →" })).toHaveAttribute(
      "href", expect.stringMatching(/^\/agencies\/8\/why\?.*routes=R/),
    );
  });

  it("says a route the lists leave out is not ranked, and one outside the overview's few is not among them", () => {
    reports({ on_time: [["R", null, "80.0", "0.5", 60, true, 9]] });
    overview([{ route_code: "A", share_pct: 9 }]);
    const strip = render();
    expect(strip).toHaveTextContent("Mean delay0.5 minNot ranked: too few observations, or left out of the route lists");
    expect(strip).toHaveTextContent("Share of all lateness—Not among the top 1 routes");
  });
});
