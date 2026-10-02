import { WEEKDAYS } from "../api/scope";

/** Structural so both i18next's `TFunction` and the narrower `t` shapes that
 *  components thread through helpers are accepted. */
export type LabelT = (key: string, opts?: Record<string, unknown>) => string;

/** Looks `key` up in the main `translation` resource (ja.json/en.json),
 *  never a feature namespace like "design": callers bound to a different
 *  default namespace (e.g. `useTranslation("design")`) must still resolve
 *  the real copy, not the bare key string. */
export function translationT(t: LabelT, key: string, options?: Record<string, unknown>): string {
  return t(key, { ns: "translation", ...options });
}

/** The one mapping from filter query-contract values to display text.
 *  Unknown values render as the raw value rather than being guessed into a
 *  known label. */
export function serviceValueLabel(service: string, t: LabelT): string {
  return serviceLabel(service, t).text;
}

/** A service's display text, and whether it is a translation. A service is an
 *  agency's own calendar name (平日, 秋彼岸, お盆臨時 20日); the common ones
 *  have copy under `common.service_value`, any other is shown as the agency
 *  wrote it. */ // i18n-ignore: JSDoc examples
export function serviceLabel(service: string, t: LabelT): { text: string; translated: boolean } {
  const text = translationT(t, `common.service_value.${service}`, { defaultValue: "" });
  return text ? { text, translated: true } : { text: service, translated: false };
}

export function dowValueLabel(dow: string, t: LabelT): string {
  if (dow === "weekday") return translationT(t, "common.service_value.平日"); // i18n-ignore: query contract
  if (dow === "weekend") return translationT(t, "common.service_value.土日祝"); // i18n-ignore: query contract
  const days = dow.split(",");
  if (days.every((day) => (WEEKDAYS as readonly string[]).includes(day))) {
    return days.map((day) => translationT(t, `forecast.dow_${day}`)).join(translationT(t, "common.list_separator"));
  }
  return dow;
}

export function timeBandValueLabel(timeBand: string, t: LabelT): string {
  const key = `filters.time_band.${timeBand}`;
  const label = translationT(t, key);
  return label === key ? timeBand : label;
}
