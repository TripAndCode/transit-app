import type { TFunction } from "i18next";

/** An average delay in words, late or early, so a phrase never carries a
 *  signed figure. A value that rounds to zero reads as late. */
export function avgDelayText(t: TFunction, minutes: number): string {
  const tenths = Math.round(minutes * 10);
  const min = (Math.abs(tenths) / 10).toFixed(1);
  return tenths < 0 ? t("forecast.avg_early", { min }) : t("forecast.avg_late", { min });
}
