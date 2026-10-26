import { describe, it, expect } from "vitest";
import en from "./locales/en.json";
import ja from "./locales/ja.json";

// The compare ranking and the dow reports split days by the agency's day
// types, so their weekend side is 土日祝: public holidays and any other day the
// timetable runs on its weekend or holiday service count there.
const DAY_TYPE_STRINGS: Record<string, string> = {
  "compare.mode_periods": ja.compare.mode_periods,
  "reports.type.compare_ranking": ja.reports.type.compare_ranking,
  "reports.type.dow_weekend": ja.reports.type.dow_weekend,
  "reports.direction.weekend_higher": ja.reports.direction.weekend_higher,
  "reports.direction.weekday_higher": ja.reports.direction.weekday_higher,
  "reports.desc.compare_ranking": ja.reports.desc.compare_ranking,
  "reports.desc.dow_weekday": ja.reports.desc.dow_weekday,
  "reports.desc.dow_weekend": ja.reports.desc.dow_weekend,
  "reports.hint.dow_body": ja.reports.hint.dow_body,
  "reports.dwell_run.compare_day_types": ja.reports.dwell_run.compare_day_types,
  "palette.nav_compare_sublabel": ja.palette.nav_compare_sublabel,
  "ask.col.kyujitsu_min": ja.ask.col.kyujitsu_min,
};

// The dow=weekend filter, and the Overview's peak-hour pair built on it, keep
// ISO Saturday and Sunday only, so their weekend side is 土日.
const SAT_SUN_STRINGS: Record<string, string> = {
  "ask.filter_bar.dow_weekend": ja.ask.filter_bar.dow_weekend,
  "overview.peak_hour.weekend_empty": ja.overview.peak_hour.weekend_empty,
  "overview.modal.peak_hour": ja.overview.modal.peak_hour,
  "overview.peak_explainer": ja.overview.peak_explainer,
};

describe("ja day-group wording", () => {
  it.each(Object.entries(DAY_TYPE_STRINGS))("%s calls the weekend side 土日祝", (_key, text) => {
    expect(text).toContain("土日祝");
    expect(text).not.toMatch(/土日(?!祝)/);
  });

  it.each(Object.entries(SAT_SUN_STRINGS))("%s calls the Sat/Sun split 土日, not 土日祝", (_key, text) => {
    expect(text).toContain("土日");
    expect(text).not.toContain("土日祝");
  });
});

describe("en day-group wording", () => {
  it("names holidays on the day-type side and leaves them out of the Sat/Sun filter", () => {
    expect(en.reports.type.compare_ranking).toMatch(/holiday/i);
    expect(en.reports.desc.dow_weekday).toMatch(/holiday/i);
    expect(en.ask.filter_bar.dow_weekend).not.toMatch(/holiday/i);
    expect(en.overview.peak_explainer).not.toMatch(/weekend\/holiday/i);
  });
});
