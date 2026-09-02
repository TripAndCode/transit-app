import type { TFunction } from "i18next";

/** Signed whole-minute delay label via the shared i18n unit key (e.g. "+3 min" / "-2 min").
 *  The sign is derived from the value itself, never hardcoded by the caller, so a
 *  route that runs early on average (a negative value) renders a single "-" rather
 *  than a caller-supplied "+" colliding with the negative sign already in the number. */
export function signedMin(sec: number, t: TFunction): string {
  const m = Math.round(sec / 60);
  return t("common.unit_min_signed", { sign: sec < 0 ? "-" : "+", value: Math.abs(m) });
}
