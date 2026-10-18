import type { TFunction } from "i18next";
import { fixedTenth, roundTenth } from "./format";

/** An average delay in words, late or early, so a phrase never carries a
 *  signed figure. A value that rounds to zero reads as late. */
export function avgDelayText(t: TFunction, minutes: number): string {
  const rounded = roundTenth(minutes);
  const min = fixedTenth(Math.abs(rounded));
  return rounded < 0 ? t("forecast.avg_early", { min }) : t("forecast.avg_late", { min });
}
