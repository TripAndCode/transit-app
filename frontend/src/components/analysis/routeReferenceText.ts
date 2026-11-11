import type { TFunction } from "i18next";
import type { RouteReference, RouteReferences } from "../../api/types";
import { fixedTenth, formatDateRange, formatNumber } from "../../utils/format";
import { avgDelayReferenceJudgement, onTimeReferenceJudgement } from "../overview/storySentence";

/** The figure a reference stands beside. */
export type ReferenceFigure = "on_time" | "avg";

export const rangeOf = (refs: RouteReferences) => formatDateRange(refs.window_from, refs.window_to, { year: false });

/** Why a reference has no figure, in Pulse's words for the same rule. A route
 *  list has no aggregate by date under a time band, where Pulse's headline
 *  still has one, or under an on-time window the aggregates cannot split, so
 *  that reason names which. */
export function reasonText(refs: RouteReferences, ref: Pick<RouteReference, "reason" | "samples">, timeBand: string, t: TFunction) {
  const vars = { days: refs.days, range: rangeOf(refs) };
  switch (ref.reason) {
    case "no_data":
      return t("overview.reference.no_data", vars);
    case "insufficient_days":
      return t("overview.reference.insufficient_days", { ...vars, count: refs.days_collected, min: refs.min_days });
    case "insufficient_samples":
      return t("overview.reference.insufficient_samples", {
        ...vars,
        samples: formatNumber(ref.samples),
        min: formatNumber(refs.min_samples),
      });
    case "not_aggregated":
      return t(timeBand === "all" ? "routeReference.not_aggregated_window" : "routeReference.not_aggregated_band", vars);
    case null:
      return null;
  }
}

/** The reference's figure, labelled with its window and, when the feed missed
 *  some of the days the scope selects, how many it had. */
export function valueText(refs: RouteReferences, value: number, figure: ReferenceFigure, t: TFunction): string {
  const partial = refs.days_collected < refs.days_selected;
  const vars = { days: refs.days, range: rangeOf(refs), count: refs.days_collected, value: fixedTenth(value) };
  if (figure === "avg") return t(partial ? "overview.reference.line_partial" : "overview.reference.line", vars);
  return t(partial ? "routeReference.on_time_partial" : "routeReference.on_time", vars);
}

export function judge(figure: ReferenceFigure, value: number | null, ref: RouteReference) {
  return figure === "avg"
    ? avgDelayReferenceJudgement(value, ref.avg_min)
    : onTimeReferenceJudgement(value, ref.on_time_pct);
}

/** The Routes table's mark on a route's on-time rate: how it compares with the
 *  route's own reference, with the reference itself as the mark's tip. None
 *  when the route has no reference to compare with; the legend says why. */
export function onTimeReferenceMark(refs: RouteReferences, row: unknown[], t: TFunction) {
  const ref = refs.routes[String(row[0])];
  const pct = row[2] == null ? null : Number(row[2]);
  if (refs.reason != null || ref?.reason !== null || ref.on_time_pct == null) return undefined;
  const judgement = onTimeReferenceJudgement(pct, ref.on_time_pct);
  if (judgement == null) return undefined;
  return {
    mark: t(`routeReference.chip_${judgement}`),
    tip: valueText(refs, ref.on_time_pct, "on_time", t),
    tone: "reference" as const,
  };
}
