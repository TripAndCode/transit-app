import i18n from "../i18n";

/** Locale-neutral separator for joining short filter/context fragments
 *  (e.g. route name, date range, service type) into one line. */
export const FILTER_SEPARATOR = " · "; // i18n-ignore: locale-neutral separator

function resolvedLocale(): string {
  return i18n.resolvedLanguage ?? i18n.language;
}

/** Format a number using the active UI locale. Reads the current i18n
 *  instance directly so non-React callers don't need to thread a locale. */
export function formatNumber(n: number, opts?: Intl.NumberFormatOptions): string {
  return new Intl.NumberFormat(resolvedLocale(), opts).format(n);
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
  return new Intl.DateTimeFormat(resolvedLocale(), opts).format(date);
}
