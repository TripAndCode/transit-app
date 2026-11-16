import { describe, expect, it } from "vitest";
import { cardHome } from "./cardHome";

const DATES = { from_date: "2026-06-01", to_date: "2026-06-07" };
const CONDITIONS = { dow: "weekday", time_band: "morning", service: "all" };

describe("cardHome", () => {
  it("opens a top-N delay card on the Routes table over the card's dates and conditions", () => {
    expect(cardHome(8, "top_n", { metric: "avg_delay", n: 10, ...DATES }, CONDITIONS)).toEqual({
      screen: "routes",
      href: "/agencies/8/routes?from=2026-06-01&to=2026-06-07&dow=weekday&time_band=morning&sort=avg",
    });
  });

  it("keeps a best-first delay card's order", () => {
    expect(cardHome(8, "top_n", { metric: "avg_delay", best_first: true, ...DATES }, null)?.href).toBe(
      "/agencies/8/routes?from=2026-06-01&to=2026-06-07&sort=avg&order=best",
    );
  });

  it("opens a top-N on-time card highest first, as the card lists it", () => {
    expect(cardHome(8, "top_n", { metric: "on_time_rate", ...DATES }, null)?.href).toBe(
      "/agencies/8/routes?from=2026-06-01&to=2026-06-07&order=best&sort=on_time",
    );
  });

  it("opens a top-N 5-minute card on the late-departure count", () => {
    expect(cardHome(8, "top_n", { metric: "worst_5min", ...DATES }, null)?.href).toBe(
      "/agencies/8/routes?from=2026-06-01&to=2026-06-07&sort=late5_count",
    );
  });

  it("opens an on-time ranking card in its order, under its threshold", () => {
    expect(cardHome(8, "on_time", { best_first: false, threshold_min: 3, ...DATES }, CONDITIONS)?.href).toBe(
      "/agencies/8/routes?from=2026-06-01&to=2026-06-07&dow=weekday&time_band=morning&late=180&early=any&sort=on_time",
    );
    expect(cardHome(8, "on_time_rate", { ...DATES }, null)?.href).toBe(
      "/agencies/8/routes?from=2026-06-01&to=2026-06-07&order=best&sort=on_time",
    );
  });

  it("opens a weekday-against-weekend card on Day types & agencies, with its route", () => {
    expect(cardHome(8, "cmp_service", { dimension: "dow", route: "R1", ...DATES }, CONDITIONS)).toEqual({
      screen: "compare",
      href: "/agencies/8/compare?from=2026-06-01&to=2026-06-07&dow=weekday&time_band=morning&routes=R1&by=periods",
    });
  });

  it("has no home for one route's split by every day type", () => {
    expect(cardHome(8, "compare_segments", { dimension: "service_type", route: "R1", ...DATES }, null)).toBeNull();
  });

  it("opens a trend card on Time's trend, with its route", () => {
    expect(cardHome(8, "trend", { route: "R1", ...DATES }, null)).toEqual({
      screen: "time",
      href: "/agencies/8/time?from=2026-06-01&to=2026-06-07&routes=R1&report=trend",
    });
  });

  it("has no home for a card the rail screens do not answer", () => {
    expect(cardHome(8, "route_stats", { route: "R1", ...DATES }, null)).toBeNull();
    expect(cardHome(8, null, null, null)).toBeNull();
  });

  it("drops a value the scope would not accept rather than carrying it", () => {
    expect(cardHome(8, "trend", { from_date: "yesterday", to_date: "2026-06-07" }, null)?.href).toBe(
      "/agencies/8/time?to=2026-06-07&report=trend",
    );
  });
});
