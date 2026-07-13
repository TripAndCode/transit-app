import { describe, it, expect } from "vitest";
import { groupBySeverityBand } from "./routesToCheckBands";
import type { OverviewTopDelayedRoute } from "../api/types";

function route(avg_min: number, route_code = "R"): OverviewTopDelayedRoute {
  return { route_code, route_short_name: null, avg_min };
}

describe("groupBySeverityBand", () => {
  it("groups routes into worst-first bands, omitting empty ones", () => {
    const groups = groupBySeverityBand([route(6.6, "A"), route(5.7, "B"), route(2.1, "C")]);
    expect(groups.map((g) => g.band)).toEqual(["severe", "mild"]);
    expect(groups[0].routes.map((r) => r.route_code)).toEqual(["A", "B"]);
    expect(groups[1].routes.map((r) => r.route_code)).toEqual(["C"]);
  });

  it("uses the exact delayColor() thresholds via the shared delayBand() classifier: 1.5-3 mild, 3-5 moderate, >=5 severe", () => {
    const groups = groupBySeverityBand([route(1.5), route(2.9), route(3), route(4.9), route(5)]);
    const byBand = Object.fromEntries(groups.map((g) => [g.band, g.routes.length]));
    expect(byBand.mild).toBe(2);
    expect(byBand.moderate).toBe(2);
    expect(byBand.severe).toBe(1);
  });

  it("excludes the ok band (<1.5 min) entirely — the backend's worst-N query has no floor, so a healthy agency's list must not show a route this app's own ramp considers fine", () => {
    const groups = groupBySeverityBand([route(4.0, "A"), route(1.4, "B"), route(0.5, "C")]);
    expect(groups.map((g) => g.band)).toEqual(["moderate"]);
    expect(groups.flatMap((g) => g.routes.map((r) => r.route_code))).toEqual(["A"]);
  });

  it("returns an empty array for an empty input", () => {
    expect(groupBySeverityBand([])).toEqual([]);
  });
});
