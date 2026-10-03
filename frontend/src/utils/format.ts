import type { TFunction } from "i18next";
import i18n from "../i18n";

/** Locale-neutral separator for joining short filter/context fragments
 *  (e.g. route name, date range, service type) into one line. */
export const FILTER_SEPARATOR = " · "; // i18n-ignore: locale-neutral separator

/** Placeholder for a missing/inapplicable value in a table or detail row. */
export const EM_DASH = "—"; // i18n-ignore: locale-neutral placeholder

function resolvedLocale(): string {
  return i18n.resolvedLanguage ?? i18n.language;
}

// Building an Intl formatter costs far more than formatting with one, and
// charts and tables format once per point or row; one instance per
// language and options serves them all.
const numberFormats = new Map<string, Intl.NumberFormat>();
const dateFormats = new Map<string, Intl.DateTimeFormat>();

function numberFormat(opts?: Intl.NumberFormatOptions): Intl.NumberFormat {
  const locale = resolvedLocale();
  const key = `${locale}|${JSON.stringify(opts ?? {})}`;
  let format = numberFormats.get(key);
  if (!format) {
    format = new Intl.NumberFormat(locale, opts);
    numberFormats.set(key, format);
  }
  return format;
}

function dateFormat(opts: Intl.DateTimeFormatOptions): Intl.DateTimeFormat {
  const locale = resolvedLocale();
  const key = `${locale}|${JSON.stringify(opts)}`;
  let format = dateFormats.get(key);
  if (!format) {
    format = new Intl.DateTimeFormat(locale, opts);
    dateFormats.set(key, format);
  }
  return format;
}

/** Format a number using the active UI locale. Reads the current i18n
 *  instance directly so non-React callers don't need to thread a locale. */
export function formatNumber(n: number, opts?: Intl.NumberFormatOptions): string {
  return numberFormat(opts).format(n);
}

// Intl.DateTimeFormat renders a date-only string when given no component
// options at all -- unlike the legacy Date.prototype.toLocaleString(), which
// defaults to date + time. Callers of formatDateTime() expect a full
// timestamp unless they ask for something narrower.
const DEFAULT_DATETIME_OPTS: Intl.DateTimeFormatOptions = { dateStyle: "medium", timeStyle: "short" };

/** Format an ISO date/time string using the active UI locale. Returns "—"
 *  for input that does not parse to a valid date. */
export function formatDateTime(iso: string, opts: Intl.DateTimeFormatOptions = DEFAULT_DATETIME_OPTS): string {
  const date = new Date(iso);
  if (isNaN(date.getTime())) return "—";
  return dateFormat(opts).format(date);
}

/** Formats a value already expressed on a 0-100 percent scale (e.g. the
 *  API's `on_time_pct`, `service_delivered_pct`). `t` is unused but kept so
 *  this matches every other column formatter's `(v, t) => string` shape. */
export function fmtPct(v: unknown, _t: TFunction): string {
  if (v == null) return EM_DASH;
  const n = Number(v);
  if (!isFinite(n)) return EM_DASH;
  return `${n.toFixed(1)}%`;
}

/** Formats a 0..1 ratio (e.g. `long_gap_rate`) as a percentage. Not
 *  interchangeable with {@link fmtPct}, which expects its input already
 *  scaled to 0-100. */
export function fmtRatioPct(v: number | null): string {
  return v == null ? EM_DASH : `${(v * 100).toFixed(1)}%`;
}

/** Minutes with the unit spaced as the language spaces it ("6.3 min",
 *  "6.3分"), at one decimal: the precision every delay figure uses. */ // i18n-ignore: JSDoc examples
export function formatMinutes(v: number | null | undefined, digits = 1): string {
  if (v == null || !isFinite(v)) return EM_DASH;
  const value = formatNumber(v, { minimumFractionDigits: digits, maximumFractionDigits: digits });
  return i18n.t("common.minutes_value", { value });
}

/** A delay held in seconds, read as minutes and seconds ("5 min 55 s"). */
export function formatDuration(seconds: number | null | undefined): string {
  if (seconds == null || !isFinite(seconds)) return EM_DASH;
  const total = Math.round(Math.abs(seconds));
  const sign = seconds < 0 && total > 0 ? "-" : "";
  const min = Math.floor(total / 60);
  const sec = total % 60;
  const text =
    min === 0
      ? i18n.t("common.duration_sec", { sec })
      : sec === 0
        ? i18n.t("common.duration_min", { min })
        : i18n.t("common.duration_min_sec", { min, sec });
  return sign + text;
}

/** A calendar date from an ISO `YYYY-MM-DD` string, at local midnight: an
 *  ISO date parsed by `Date` is UTC midnight, which is the previous day in
 *  any zone west of UTC. */
function parseIsoDate(iso: string): Date | null {
  const m = /^(\d{4})-(\d{2})-(\d{2})/.exec(iso);
  if (!m) return null;
  const date = new Date(Number(m[1]), Number(m[2]) - 1, Number(m[3]));
  return isNaN(date.getTime()) ? null : date;
}

function dayFormat(withYear: boolean): Intl.DateTimeFormat {
  // Japanese writes the month as 9月, which is its "long" form; "short" is
  // a bare number there.
  const month = resolvedLocale().startsWith("ja") ? "long" : "short";
  return dateFormat(withYear ? { year: "numeric", month, day: "numeric" } : { month, day: "numeric" });
}

/** One day in the language's date style ("Sep 29, 2026", "2026年9月29日"). */ // i18n-ignore: JSDoc examples
export function formatDate(iso: string, { year = true }: { year?: boolean } = {}): string {
  const date = parseIsoDate(iso);
  return date ? dayFormat(year).format(date) : EM_DASH;
}

/** A period, with its year written once, on the side the language writes it
 *  ("Sep 2 – Oct 1, 2026", "2026年9月2日〜10月1日"); `year: false` drops it
 *  only within one year, where it says nothing. Built from two dates
 *  rather than Intl's formatRange, whose Japanese ranges fall back to a
 *  slashed numeric form unlike its single dates. */ // i18n-ignore: JSDoc examples
export function formatDateRange(from: string, to: string, { year = true }: { year?: boolean } = {}): string {
  const a = parseIsoDate(from);
  const b = parseIsoDate(to);
  if (!a || !b) return EM_DASH;
  if (from.slice(0, 10) === to.slice(0, 10)) return dayFormat(year).format(a);
  const sameYear = a.getFullYear() === b.getFullYear();
  if (!sameYear) {
    const withYear = dayFormat(true);
    return i18n.t("common.date_range", { from: withYear.format(a), to: withYear.format(b) });
  }
  if (!year) {
    const noYear = dayFormat(false);
    return i18n.t("common.date_range", { from: noYear.format(a), to: noYear.format(b) });
  }
  const yearFirst = dayFormat(true).formatToParts(a)[0]?.type === "year";
  return i18n.t("common.date_range", {
    from: dayFormat(yearFirst).format(a),
    to: dayFormat(!yearFirst).format(b),
  });
}

/** A chart tick's date: month and day, as numbers ("9/28"). */
export function formatShortDate(iso: string): string {
  const date = parseIsoDate(iso);
  return date ? dateFormat({ month: "numeric", day: "numeric" }).format(date) : EM_DASH;
}

/** An hour of the day as the clock range it covers ("17:00–18:00"). */
export function formatHourRange(hour: number): string {
  const clock = (h: number) => `${String(h).padStart(2, "0")}:00`;
  return `${clock(hour)}–${clock(hour + 1)}`;
}
