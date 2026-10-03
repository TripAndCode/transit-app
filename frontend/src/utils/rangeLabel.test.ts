// @vitest-environment node
import { describe, it, expect } from "vitest";
import i18n from "../i18n";
import { rangeLabel } from "./rangeLabel";
import type { FilterCtx } from "../api/types";

const baseCtx: FilterCtx = {
  dow: "all",
  time_band: "all",
  routes: [],
};

const t = (key: string) =>
  ({
    "filters.range.last_7d": "Last 7 days",
    "filters.range.last_30d": "Last 30 days",
    "filters.range.last_90d": "Last 90 days",
  })[key] ?? key;

describe("rangeLabel", () => {
  it("returns null when there is no from/to date", () => {
    expect(rangeLabel(baseCtx, t)).toBeNull();
  });

  it("recognizes the 7-day preset by day count", () => {
    expect(rangeLabel({ ...baseCtx, from_date: "2026-07-01", to_date: "2026-07-07" }, t)).toBe("Last 7 days");
  });

  it("recognizes the 30-day preset by day count", () => {
    expect(rangeLabel({ ...baseCtx, from_date: "2026-06-01", to_date: "2026-06-30" }, t)).toBe("Last 30 days");
  });

  it("recognizes the 90-day preset by day count", () => {
    expect(rangeLabel({ ...baseCtx, from_date: "2026-04-01", to_date: "2026-06-30" }, t)).toBe("Last 90 days");
  });

  it("writes any other range in the language's date style", async () => {
    await i18n.changeLanguage("en");
    expect(rangeLabel({ ...baseCtx, from_date: "2026-06-01", to_date: "2026-07-15" }, t)).toBe("Jun 1 – Jul 15, 2026");
  });
});
