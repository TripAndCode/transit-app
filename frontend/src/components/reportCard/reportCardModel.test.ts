import { describe, expect, it } from "vitest";
import i18n from "../../i18n";
import { formatFigure, formatInterval, gradePoint, missingText, notGradedText, recentMonths } from "./reportCardModel";

const t = i18n.getFixedT("en");

type Gated = Parameters<typeof notGradedText>[1];

const card: Gated = {
  not_graded: null,
  observations: 50,
  floor: 100,
  coverage_pct: 41,
  on_time_ci_low: 70.1,
  on_time_ci_high: 80.4,
  missing: [],
};

describe("reportCardModel", () => {
  it("prints rates in percent and excess wait in minutes, to a tenth", () => {
    expect(formatFigure(t, "bunching_pct", 9)).toBe("9.0%");
    expect(formatFigure(t, "ewt_min", 0.4)).toBe("0.4 min");
    expect(formatFigure(t, "on_time_pct", null)).toBe("—");
  });

  it("prints an interval only when both ends are known", () => {
    expect(formatInterval(70.1, 80.4)).toBe("70.1–80.4");
    expect(formatInterval(null, 80.4)).toBeNull();
  });

  it("lists a year of months back from the newest closed one, across a year end", () => {
    const months = recentMonths("2026-02", 4);
    expect(months).toEqual(["2026-02", "2026-01", "2025-12", "2025-11"]);
    expect(recentMonths("2026-10")).toHaveLength(12);
  });

  it("draws A highest and F lowest", () => {
    expect(gradePoint("A")).toBeGreaterThan(gradePoint("B"));
    expect(gradePoint("F")).toBe(0);
  });

  it("names the figure a gate read when a route has no grade", () => {
    expect(notGradedText(t, { ...card, not_graded: "coverage_low" })).toBe("N/A: observation coverage 41.0%");
    expect(notGradedText(t, { ...card, not_graded: "too_few_observations" })).toBe("N/A: 50 observations, under 100");
    expect(notGradedText(t, { ...card, not_graded: "ci_wide" })).toBe("N/A: on-time interval too wide (70.1–80.4)");
    expect(
      notGradedText(t, { ...card, not_graded: "not_judged", missing: [{ metric: "bunching_pct", reason: "not_measured" }] }),
    ).toBe("N/A: not measured: Bunching");
    expect(notGradedText(t, { ...card, not_graded: "no_data" })).toBe("N/A: not observed this month");
    expect(notGradedText(t, card)).toBe("");
  });

  it("says why a figure is missing, or the reason as sent when it has no words", () => {
    expect(missingText(t, "feed_reports_no_cancellations")).toBe("feed reports no cancellations");
    expect(missingText(t, "something_new")).toBe("something_new");
  });
});
