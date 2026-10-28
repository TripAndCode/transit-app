import type { TFunction } from "i18next";
import { fixedTenth, roundTenth } from "./format";

/** A forecast cell's 10th-90th percentile range in words, which holds about
 *  8 runs in 10 by design: "10回中8回は 2.0〜11.0分の範囲". A lower bound
 *  below zero is early running and reads as such. The point forecast and
 *  the range come from different models, so a point can fall outside its
 *  range; the two would then contradict each other, and no range is given. */
export function forecastRangeText(t: TFunction, point: number, q10: number | null | undefined, q90: number | null | undefined): string | null {
  if (q10 == null || q90 == null || !Number.isFinite(q10) || !Number.isFinite(q90)) return null;
  const [low, high, at] = [roundTenth(q10), roundTenth(q90), roundTenth(point)];
  if (low > high || at < low || at > high) return null;
  const abs = (v: number) => fixedTenth(Math.abs(v));
  const range =
    low >= 0
      ? t("forecast.range_late", { low: abs(low), high: abs(high) })
      : high <= 0
        ? t("forecast.range_early", { low: abs(high), high: abs(low) })
        : t("forecast.range_across", { early: abs(low), late: abs(high) });
  return t("forecast.range_sentence", { range });
}
