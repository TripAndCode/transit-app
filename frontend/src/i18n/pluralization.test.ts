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
    expect(i18n.t("filters.routes.variant_count", { count: 1 })).toBe("(1 route)");
    expect(i18n.t("filters.routes.variant_count", { count: 2 })).toBe("(2 routes)");
  });

  it("overview.concentration_legend uses the singular noun for count=1", () => {
    expect(i18n.t("overview.concentration_legend", { count: 1, pct: 40 })).toBe("Top 1 route carries 40% of the delay.");
    expect(i18n.t("overview.concentration_legend", { count: 2, pct: 40 })).toBe("Top 2 routes carry 40% of the delay.");
  });

  it("overview.concentration_rest uses the singular noun for count=1", () => {
    expect(i18n.t("overview.concentration_rest", { count: 1, rest: 5 })).toBe("Remaining 1 route: 5%");
    expect(i18n.t("overview.concentration_rest", { count: 2, rest: 5 })).toBe("Remaining 2 routes: 5%");
  });
});
