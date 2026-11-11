import { afterEach, describe, expect, it, vi } from "vitest";
import { screen, within } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { renderWithProviders } from "../../test/renderWithProviders";
import * as hooks from "../../api/hooks";
import { SCOPE_EXTRAS_NONE, type Scope } from "../../api/scope";
import type { RouteReference, RouteReferences } from "../../api/types";
import { RouteSummaryStrip } from "./RouteSummaryStrip";

const CTX: Scope = {
  ...SCOPE_EXTRAS_NONE, from: "2026-09-24", to: "2026-10-23", dow: "all", time_band: "all", service: "all", routes: ["R"],
};
const DEFINITION = {
  preset: null, early_tolerance_sec: null, late_tolerance_sec: 60, early_departure_sec: null, exclusion_threshold_sec: 7200,
  measurement_point: "all_stops_all_observations", dedup_rule: "latest_observation_per_stop_event",
  method_version: "2026.11.0", source: "aggregates", built_at: null, value_basis: "feed_prediction",
  timetable_versions: null,
  source_mix: null,
};

function reports(byType: Record<string, unknown[]>, rowsTotal = 174, pending: string[] = [], reference: RouteReferences | null = null) {
  return vi.spyOn(hooks, "useReport").mockImplementation((_id, type) => (pending.includes(type ?? "") ? { data: undefined, isPending: true } : {
    data: {
      report_type: type,
      rows: byType[type ?? ""] ?? [],
      rows_total: rowsTotal,
      definition: DEFINITION,
      ...(type === "on_time" ? { reference } : null),
    },
    isPending: false,
  }) as never);
}

/** The 90 days before the page's 2026-09-24 start, route R's figures over
 *  them as `route` gives them. */
function reference(route: Partial<RouteReference>): RouteReferences {
  return {
    samples: 400_000, window_from: "2026-06-26", window_to: "2026-09-23", days: 90, days_selected: 90,
    days_collected: 90, min_days: 72, min_samples: 100, reason: null,
    routes: { R: { on_time_pct: null, avg_min: null, samples: 400_000, reason: null, ...route } },
  };
}

const FIGURES = {
  ranking: [["A", null, "5.1", "4", "9", 500], ["R", null, "3.06", "2", "8", 143516]],
  on_time: [["R", null, "49.0", "3.06", 143516, false, 0.3]],
  worst_5min: [["R", null, 26192, "3.06", 143516]],
};

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

  it("says a route observed too rarely to rank is not ranked, and one outside the overview's few is not among them", () => {
    reports({ on_time: [["R", null, "80.0", "0.5", 60, true, 9]] });
    overview([{ route_code: "A", share_pct: 9 }]);
    const strip = render();
    expect(strip).toHaveTextContent("Mean delay0.5 minNot ranked: too few observations");
    expect(strip).toHaveTextContent("Share of all lateness—Not among the top 1 routes");
  });

  it("says a route no list names is not in the route lists", () => {
    reports({});
    overview([]);
    expect(render()).toHaveTextContent("Mean delay—Not in the route lists: too few observations, or left out of them");
  });

  it("waits for the ranking before saying anything about a rank", () => {
    reports({ on_time: [["R", null, "49.0", "3.06", 143516, false, 0.3]] }, 174, ["ranking"]);
    overview([]);
    const strip = render();
    expect(strip).toHaveTextContent("Mean delay…");
    expect(strip).not.toHaveTextContent("Not ranked");
  });

  it("names the route's own past 90 days beside its mean delay and on-time rate, judged against each", () => {
    reports(FIGURES, 174, [], reference({ on_time_pct: 45.0, avg_min: 2.5 }));
    overview([]);
    const strip = render();
    const [avg, onTime] = within(strip).getAllByRole("definition");
    expect(avg).toHaveTextContent(/Past 90 days \(.+\): average delay 2\.5 min\s*Worse than the past 90 days$/);
    expect(onTime).toHaveTextContent(/^49%Past 90 days \(.+\): on time 45\.0%\s*Better than the past 90 days/);
    expect(within(onTime).getByText("Better than the past 90 days")).toHaveClass("reference-chip");
    // The rules' popover is offered once, beside the on-time rate.
    expect(within(onTime).getAllByRole("button")).toHaveLength(1);
    expect(within(avg).queryByRole("button")).not.toBeInTheDocument();
  });

  it("says once, under the on-time rate, why the route has no past 90 days to compare with", () => {
    reports(FIGURES, 174, [], reference({ samples: 90, reason: "insufficient_samples" }));
    overview([]);
    const strip = render();
    expect(within(strip).getAllByText(/only 90 observations, short of the 100 needed for a usual level/)).toHaveLength(1);
    const [, onTime] = within(strip).getAllByRole("definition");
    expect(onTime).toHaveTextContent(/only 90 observations/);
    expect(within(strip).queryByText(/(Better|Worse) than the past 90 days|About the same as/)).not.toBeInTheDocument();
  });

  it("names no past 90 days for a route the reference does not list", () => {
    reports(FIGURES, 174, [], { ...reference({}), routes: {} });
    overview([]);
    expect(render()).not.toHaveTextContent(/past 90 days/i);
  });
});
