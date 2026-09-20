import { describe, it, expect } from "vitest";
import { REPORT_GROUPS, groupReports, reportLabel, reportDescriptionKey } from "./reportGroups";
import en from "../../i18n/locales/en.json";
import ja from "../../i18n/locales/ja.json";

describe("groupReports", () => {
  it("keeps the four groups in a fixed reading order and drops the empty ones", () => {
    const groups = groupReports(["trend", "ranking"]);
    expect(groups.map((g) => g.key)).toEqual(["ranking", "pattern"]);
    expect(groups.map((g) => g.types)).toEqual([["ranking"], ["trend"]]);
  });

  it("orders reports within a group by the group's own order, not the API's", () => {
    const groups = groupReports(["compare_ranking", "ranking_best", "ranking"]);
    expect(groups).toHaveLength(1);
    expect(groups[0].types).toEqual(["ranking", "ranking_best", "compare_ranking"]);
  });

  it("carries an unrecognised report type through under 'other' rather than dropping it", () => {
    const groups = groupReports(["ranking", "brand_new_report"]);
    expect(groups.map((g) => g.key)).toEqual(["ranking", "other"]);
    expect(groups.at(-1)!.types).toEqual(["brand_new_report"]);
  });

  it("includes route_forecast as ordinary group data, not a special case", () => {
    const groups = groupReports(["route_forecast"]);
    expect(groups).toEqual([{ key: "forecast", types: ["route_forecast"] }]);
  });

  it("lists every known report type exactly once across the groups", () => {
    const all = Object.values(REPORT_GROUPS).flat();
    expect(new Set(all).size).toBe(all.length);
    expect(all).toContain("delay_certificate");
  });
});

describe("labels and descriptions", () => {
  const t = ((key: string) => key) as unknown as Parameters<typeof reportLabel>[0];

  it("falls back to the raw report type when there is no label for it", () => {
    expect(reportLabel(t, "ranking")).toBe("reports.type.ranking");
    expect(reportLabel(((k: string, o?: { defaultValue?: string }) => o?.defaultValue ?? k) as never, "mystery")).toBe(
      "mystery",
    );
  });

  it("has a one-line description in both languages for every known report type", () => {
    for (const type of Object.values(REPORT_GROUPS).flat()) {
      const key = reportDescriptionKey(type).replace("reports.desc.", "");
      expect(en.reports.desc, `en ${type}`).toHaveProperty(key);
      expect(ja.reports.desc, `ja ${type}`).toHaveProperty(key);
    }
  });

  it("names every group in both languages", () => {
    for (const key of Object.keys(REPORT_GROUPS)) {
      expect(en.reports.group, `en ${key}`).toHaveProperty(key);
      expect(ja.reports.group, `ja ${key}`).toHaveProperty(key);
    }
    expect(en.reports.group).toHaveProperty("other");
    expect(ja.reports.group).toHaveProperty("other");
  });
});
