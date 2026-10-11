import { describe, it, expect } from "vitest";
import ja from "./locales/ja.json";

// The weekday-vs-weekend compare ranking splits on Saturday/Sunday only, so its
// Japanese copy says 土日; 土日祝 stays reserved for the timetable service type.
const COMPARE_STRINGS: Record<string, string> = {
  "compare.mode_periods": ja.compare.mode_periods,
  "reports.type.compare_ranking": ja.reports.type.compare_ranking,
  "reports.direction.weekend_higher": ja.reports.direction.weekend_higher,
  "reports.direction.weekday_higher": ja.reports.direction.weekday_higher,
  "reports.desc.compare_ranking": ja.reports.desc.compare_ranking,
  "reports.dwell_run.compare_day_types": ja.reports.dwell_run.compare_day_types,
  "palette.nav_compare_sublabel": ja.palette.nav_compare_sublabel,
};

describe("ja compare wording", () => {
  it.each(Object.entries(COMPARE_STRINGS))("%s does not call the Sat/Sun split 土日祝", (_key, text) => {
    expect(text).toBeTruthy();
    expect(text).not.toContain("土日祝");
  });
});
