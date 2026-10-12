import { describe, it, expect, beforeAll, afterAll } from "vitest";
import i18n from ".";

describe("English pluralization", () => {
  beforeAll(async () => await i18n.changeLanguage("en"));
  afterAll(async () => await i18n.changeLanguage("ja"));

  it("peakHourModal.routeCount uses the singular noun for count=1", () => {
    expect(i18n.t("peakHourModal.routeCount", { count: 1 })).toBe("1 route");
    expect(i18n.t("peakHourModal.routeCount", { count: 2 })).toBe("2 routes");
  });

  it("filters.routes.variant_count uses the singular noun for count=1", () => {
    expect(i18n.t("filters.routes.variant_count", { count: 1 })).toBe("(1 variant)");
    expect(i18n.t("filters.routes.variant_count", { count: 2 })).toBe("(2 variants)");
  });

  it("overview.concentration_legend uses the singular noun for count=1", () => {
    expect(i18n.t("overview.concentration_legend", { count: 1, pct: 40 })).toBe("Top 1 route carries 40% of the delay.");
    expect(i18n.t("overview.concentration_legend", { count: 2, pct: 40 })).toBe("Top 2 routes carry 40% of the delay.");
  });

  it("overview.concentration_rest uses the singular noun for count=1", () => {
    expect(i18n.t("overview.concentration_rest", { count: 1, rest: 5 })).toBe("Remaining 1 route: 5%");
    expect(i18n.t("overview.concentration_rest", { count: 2, rest: 5 })).toBe("Remaining 2 routes: 5%");
  });

  it("admin.ops.agencies_stale uses the singular noun for count=1", () => {
    expect(i18n.t("admin.ops.agencies_stale", { count: 1 })).toBe("1 agency with stale aggregates");
    expect(i18n.t("admin.ops.agencies_stale", { count: 2 })).toBe("2 agencies with stale aggregates");
  });

  it("reports.raw_rows uses the singular noun for count=1", () => {
    expect(i18n.t("reports.raw_rows", { count: 1 })).toBe("Raw (1 row)");
    expect(i18n.t("reports.raw_rows", { count: 2 })).toBe("Raw (2 rows)");
  });

  it("app.feed_health.banner uses the singular noun for count=1", () => {
    expect(i18n.t("app.feed_health.banner", { count: 1 })).toBe(
      "Feed health: 1 implausible delay reading (likely a stuck or stale data feed) was filtered out over the last 7 days, so it doesn't skew the figures here.",
    );
    expect(i18n.t("app.feed_health.banner", { count: 2 })).toBe(
      "Feed health: 2 implausible delay readings (likely a stuck or stale data feed) were filtered out over the last 7 days, so they don't skew the figures here.",
    );
  });

  it("marey trip counts use the singular noun for count=1", () => {
    expect(i18n.t("mareyTripCount", { ns: "design", count: 1 })).toBe("1 trip");
    expect(i18n.t("mareyTripCount", { ns: "design", count: 2 })).toBe("2 trips");
    expect(i18n.t("mareyChartLabel", { ns: "design", count: 1, from: "07:00", to: "09:00" })).toBe(
      "Trips over time: 1 trip from 07:00 to 09:00. The table below lists the same trips.",
    );
    expect(i18n.t("mareyChartLabel", { ns: "design", count: 2, from: "07:00", to: "09:00" })).toBe(
      "Trips over time: 2 trips from 07:00 to 09:00. The table below lists the same trips.",
    );
  });
});
