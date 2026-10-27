import type { TFunction } from "i18next";
import type { TimeBand } from "./scope";

/** A time band other than "all": one of the bands the filter selects, the
 *  forecast grid's columns and the trend's banded profile. */
export type Band = Exclude<TimeBand, "all">;

/** The time bands in clock order, each covering whole hours `from` up to,
 *  not including, `to`. Mirrors api/range.py's TIME_BAND_RANGES, the one
 *  definition the time-band filter, analyze's bucketing and the server's
 *  forecast bands all read, so a band named anywhere covers the same hours. */
export const TIME_BAND_HOURS: readonly { band: Band; from: number; to: number }[] = [
  { band: "late_night", from: 0, to: 5 },
  { band: "morning", from: 5, to: 9 },
  { band: "forenoon", from: 9, to: 12 },
  { band: "noon", from: 12, to: 14 },
  { band: "afternoon", from: 14, to: 17 },
  { band: "evening", from: 17, to: 20 },
  { band: "night", from: 20, to: 24 },
];

export const BAND_ORDER: readonly Band[] = TIME_BAND_HOURS.map((b) => b.band);

/** The band an hour 0..23 falls in. */
export function bandOf(hour: number): Band {
  const hit = TIME_BAND_HOURS.find((b) => hour >= b.from && hour < b.to);
  return hit ? hit.band : "night";
}

/** The whole hours a band covers, `from` up to, not including, `to`. */
export function bandHours(band: Band): { from: number; to: number } {
  const hit = TIME_BAND_HOURS.find((b) => b.band === band);
  return hit ? { from: hit.from, to: hit.to } : { from: 0, to: 24 };
}

/** A band's short name ("夕", "Evening"). */ // i18n-ignore: JSDoc example
export function bandName(t: TFunction, band: Band): string {
  return t(`time_bands.${band}`);
}

/** A band's hours ("17〜20時", "17–20h"). */ // i18n-ignore: JSDoc example
export function bandHoursText(t: TFunction, band: Band): string {
  return t("time_bands.hours", bandHours(band));
}

/** A band named with its hours ("夕 17〜20時", "Evening 17–20h"), for a
 *  sentence that has to say which hours it means. */ // i18n-ignore: JSDoc example
export function bandLabel(t: TFunction, band: Band): string {
  return t("time_bands.named", { name: bandName(t, band), hours: bandHoursText(t, band) });
}
