import { describe, expect, it } from "vitest";
import { cardHome } from "./cardHome";

// What a card's message records since it keeps its period: the thread's
// filters as the dispatch resolved them.
const CONDITIONS = {
  dow: "weekday",
  time_band: "morning",
  service: "all",
  from_date: "2026-06-01",
  to_date: "2026-06-07",
  routes: ["R1", "R2"],
};
const SCOPE = "from=2026-06-01&to=2026-06-07&dow=weekday&time_band=morning";

describe("cardHome", () => {
  // The card templates send no dates: the thread's period, routes and
  // conditions scope them (askCardTemplates.ts).
  it("opens a top-N delay card on the Routes table over the thread's scope it was answered under", () => {
    expect(cardHome(8, "top_n", { metric: "avg_delay", k: 5 }, CONDITIONS)).toEqual({
      screen: "routes",
      href: `/agencies/8/routes?${SCOPE}&routes=R1%2CR2&sparse=1&retired=1&zero_delay=1&sort=avg`,
    });
  });

  it("lets a top-N card's own service type replace the thread's", () => {
    expect(cardHome(8, "top_n", { metric: "avg_delay", k: 5, service_type: "saturday" }, CONDITIONS)?.href).toBe(
      `/agencies/8/routes?${SCOPE}&service=%E5%9C%9F%E6%9B%9C&routes=R1%2CR2&sparse=1&retired=1&zero_delay=1&sort=avg`,
    );
  });

  it("keeps the routes the thread named even where the table leaves them out by default", () => {
    const named = new URL(cardHome(8, "top_n", { metric: "avg_delay" }, CONDITIONS)!.href, "http://x");
    expect([named.searchParams.get("retired"), named.searchParams.get("zero_delay")]).toEqual(["1", "1"]);
    const unnamed = new URL(cardHome(8, "top_n", { metric: "avg_delay" }, { ...CONDITIONS, routes: [] })!.href, "http://x");
    expect([unnamed.searchParams.has("retired"), unnamed.searchParams.has("zero_delay")]).toEqual([false, false]);
  });

  it("ranks at the card's own floor, not the table's default", () => {
    const href = new URL(cardHome(8, "top_n", { metric: "avg_delay" }, CONDITIONS)!.href, "http://x");
    expect(href.searchParams.get("sparse")).toBe("1");
  });

  it("lets a card's own dates win over the thread's, as they do at dispatch", () => {
    expect(cardHome(8, "top_n", { metric: "avg_delay", from_date: "2026-05-01", to_date: "2026-05-31" }, CONDITIONS)?.href).toBe(
      "/agencies/8/routes?from=2026-05-01&to=2026-05-31&dow=weekday&time_band=morning&routes=R1%2CR2&sparse=1&retired=1&zero_delay=1&sort=avg",
    );
  });

  it("keeps a best-first delay card's order", () => {
    expect(cardHome(8, "top_n", { metric: "avg_delay", best_first: true }, CONDITIONS)?.href).toBe(
      `/agencies/8/routes?${SCOPE}&routes=R1%2CR2&sparse=1&retired=1&zero_delay=1&sort=avg&order=best`,
    );
  });

  it("opens a top-N on-time card highest first, as the card lists it", () => {
    expect(cardHome(8, "top_n", { metric: "on_time_rate" }, CONDITIONS)?.href).toBe(
      `/agencies/8/routes?${SCOPE}&routes=R1%2CR2&sparse=1&retired=1&zero_delay=1&order=best&sort=on_time`,
    );
  });

  it("opens a top-N 5-minute card on the late-departure count", () => {
    expect(cardHome(8, "top_n", { metric: "worst_5min" }, CONDITIONS)?.href).toBe(
      `/agencies/8/routes?${SCOPE}&routes=R1%2CR2&sparse=1&retired=1&zero_delay=1&sort=late5_count`,
    );
  });

  it("opens an on-time ranking card in its order, under its threshold", () => {
    expect(cardHome(8, "on_time", { k: 5, best_first: false, threshold_min: 3 }, CONDITIONS)?.href).toBe(
      `/agencies/8/routes?${SCOPE}&routes=R1%2CR2&late=180&early=any&sparse=1&retired=1&zero_delay=1&sort=on_time`,
    );
    expect(cardHome(8, "on_time_rate", {}, CONDITIONS)?.href).toBe(
      `/agencies/8/routes?${SCOPE}&routes=R1%2CR2&sparse=1&retired=1&zero_delay=1&order=best&sort=on_time`,
    );
  });

  it("opens a weekday-against-weekend card on Day types & agencies, narrowed to the card's route", () => {
    expect(cardHome(8, "cmp_service", { metric: "avg_delay", route_code: "R9" }, CONDITIONS)).toEqual({
      screen: "compare",
      href: `/agencies/8/compare?${SCOPE}&routes=R9&by=periods`,
    });
  });

  it("has no home for one route's split by every day type", () => {
    expect(cardHome(8, "compare_segments", { dimension: "service_type", route: "R1" }, CONDITIONS)).toBeNull();
  });

  it("opens a trend card on Time's trend, narrowed to the card's route", () => {
    expect(cardHome(8, "trend", { metric: "avg_delay", route_code: "R9", granularity: "week" }, CONDITIONS)).toEqual({
      screen: "time",
      href: `/agencies/8/time?${SCOPE}&routes=R9&report=trend`,
    });
  });

  it("links nothing for a card saved before its conditions kept the period", () => {
    const legacy = { dow: "weekday", time_band: "morning", service: "all" };
    expect(cardHome(8, "top_n", { metric: "avg_delay" }, legacy)).toBeNull();
    expect(cardHome(8, "trend", { route_code: "R9" }, legacy)).toBeNull();
    expect(cardHome(8, "cmp_service", { route_code: "R9" }, null)).toBeNull();
  });

  it("has no home for a card the rail screens do not answer", () => {
    expect(cardHome(8, "route_stats", { route: "R1" }, CONDITIONS)).toBeNull();
    expect(cardHome(8, null, null, CONDITIONS)).toBeNull();
  });

  it("links nothing when the recorded period is not one the scope accepts", () => {
    expect(cardHome(8, "trend", { route_code: "R9" }, { ...CONDITIONS, from_date: "yesterday" })).toBeNull();
  });
});
