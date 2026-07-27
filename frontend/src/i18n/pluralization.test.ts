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
});
