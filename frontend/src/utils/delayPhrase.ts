import type { TFunction } from "i18next";
import { fixedTenth, roundTenth } from "./format";

/** An average delay in words, late or early, so a phrase never carries a
 *  signed figure. A value that rounds to zero reads as late. */
export function avgDelayText(t: TFunction, minutes: number): string {
  const rounded = roundTenth(minutes);
  const min = fixedTenth(Math.abs(rounded));
  return rounded < 0 ? t("forecast.avg_early", { min }) : t("forecast.avg_late", { min });
}

/** A forecast figure in words. A forecast model's figure is its point
 *  forecast, not an average of measurements, so it reads as expected. */
export function forecastDelayText(t: TFunction, minutes: number, source: "ml" | "baseline"): string {
  if (source !== "ml") return avgDelayText(t, minutes);
  const rounded = roundTenth(minutes);
  const min = fixedTenth(Math.abs(rounded));
  return rounded < 0 ? t("forecast.expected_early", { min }) : t("forecast.expected_late", { min });
}
