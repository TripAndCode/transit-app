import { describe, it, expect } from "vitest";
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
    "common.range_separator": "–",
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

  it("falls back to the literal from/to dates joined by the locale-aware separator for a non-preset range", () => {
    // The bug fixed twice in this branch: this line must use t("common.range_separator"),
    // never a hardcoded "〜", or English UI shows the Japanese wave dash.
    expect(rangeLabel({ ...baseCtx, from_date: "2026-06-01", to_date: "2026-07-15" }, t)).toBe("2026-06-01 – 2026-07-15");
  });
});
