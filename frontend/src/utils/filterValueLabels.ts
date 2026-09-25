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
  if (service === "平日" || service === "土日祝") return translationT(t, `common.service_value.${service}`); // i18n-ignore: query contract
  return service;
}

export function dowValueLabel(dow: string, t: LabelT): string {
  if (dow === "weekday") return translationT(t, "common.service_value.平日"); // i18n-ignore: query contract
  if (dow === "weekend") return translationT(t, "common.service_value.土日祝"); // i18n-ignore: query contract
  return dow;
}

export function timeBandValueLabel(timeBand: string, t: LabelT): string {
  const key = `filters.time_band.${timeBand}`;
  const label = translationT(t, key);
  return label === key ? timeBand : label;
}
