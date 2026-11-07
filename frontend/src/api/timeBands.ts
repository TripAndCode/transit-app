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

/** The band an hour falls in. A service day's clock runs past 24:00 for a
 *  departure after midnight, and its hour 24 is 00:00 on the clock, so an
 *  hour past 23 takes the band of its clock hour (24 is late night). */
export function bandOf(hour: number): Band {
  const clock = ((hour % 24) + 24) % 24;
  const hit = TIME_BAND_HOURS.find((b) => clock >= b.from && clock < b.to);
  return hit ? hit.band : "night";
}

/** The latest hour a service day's clock can reach: it runs past 23 for a
 *  departure after midnight, and GTFS writes an hour in at most two digits.
 *  Mirrors pipeline/strategies/_time.py's MAX_SERVICE_HOUR, the bound the
 *  peak-hour breakdown accepts. */
export const LAST_SERVICE_HOUR = 99;

/** How many hours a chart of the service day spans: 0 through 23, and on
 *  through the last hour past midnight (24, 25, …) that `hours` holds, so a
 *  departure after midnight is drawn after 23 and only when there is one. */
export function serviceDayHours(hours: Iterable<number>): number {
  let last = 23;
  for (const hour of hours) if (Number.isInteger(hour) && hour > last) last = hour;
  return last + 1;
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
