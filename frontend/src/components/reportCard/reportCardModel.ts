import type { TFunction } from "i18next";
import { EM_DASH, fixedTenth, formatDateTime } from "../../utils/format";
import { GRADES, type Grade, type NotGradedReason, type RouteCard, type ReportCardMetric } from "./reportCardApi";

/** The order a card lists its figures in. */
export const METRICS: readonly ReportCardMetric[] = [
  "on_time_pct",
  "early_pct",
  "ewt_min",
  "bunching_pct",
  "service_delivered_pct",
];

/** A figure as the card prints it: a rate in percent, excess wait in minutes. */
export function formatFigure(t: TFunction, metric: ReportCardMetric, value: number | null): string {
  if (value == null) return EM_DASH;
  return metric === "ewt_min" ? t("report_card.minutes", { value: fixedTenth(value) }) : `${fixedTenth(value)}%`;
}

/** A figure's 95% interval, or nothing when it has none. */
export function formatInterval(low: number | null, high: number | null): string | null {
  return low == null || high == null ? null : `${fixedTenth(low)}–${fixedTenth(high)}`;
}

/** A month `YYYY-MM` in the reader's language. */
export function formatMonth(month: string): string {
  return formatDateTime(`${month}-01T12:00:00+09:00`, { year: "numeric", month: "long", timeZone: "Asia/Tokyo" });
}

/** *latest* and the *count* - 1 months before it, newest first. */
export function recentMonths(latest: string, count = 12): string[] {
  const [year, month] = latest.split("-").map(Number);
  return Array.from({ length: count }, (_, k) => {
    const index = year * 12 + month - 1 - k;
    return `${Math.floor(index / 12)}-${String((index % 12) + 1).padStart(2, "0")}`;
  });
}

/** A letter as a height for the trend line: A highest, F lowest. */
export function gradePoint(grade: Grade): number {
  return GRADES.length - 1 - GRADES.indexOf(grade);
}

/** Why a route-month has no letter, in a sentence naming the figure the gate read. */
export function notGradedText(t: TFunction, card: Pick<RouteCard, "not_graded" | "observations" | "floor" | "coverage_pct" | "on_time_ci_low" | "on_time_ci_high" | "missing">): string {
  const reason: NotGradedReason | null = card.not_graded;
  switch (reason) {
    case "too_few_observations":
      return t("report_card.na.too_few_observations", { observations: card.observations, floor: card.floor });
    case "coverage_low":
      return t("report_card.na.coverage_low", { pct: card.coverage_pct == null ? EM_DASH : fixedTenth(card.coverage_pct) });
    case "ci_wide":
      return t("report_card.na.ci_wide", { interval: formatInterval(card.on_time_ci_low, card.on_time_ci_high) ?? EM_DASH });
    case "not_judged":
      return t("report_card.na.not_judged", {
        figures: card.missing.map((m) => t(`report_card.metric.${m.metric}`)).join(t("common.list_separator")),
      });
    case null:
      return "";
    default:
      return t(`report_card.na.${reason}`);
  }
}

/** Why one figure is missing: a known reason in words, else the code the API sent. */
export function missingText(t: TFunction, reason: string): string {
  return t(`report_card.missing.${reason}`, { defaultValue: reason });
}
