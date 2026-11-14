import type { useTranslation } from "react-i18next";
import type { CapabilityTier, ComparabilityThresholds, NetworkAgencyRow } from "../api/types";
import { fixedTenth, formatNumber } from "../utils/format";

/** The figures a reason reads: an agencies board row has them all, and the
 *  council report's verdict carries the same ones from the board's row. */
type GateFigures = Pick<
  NetworkAgencyRow,
  | "comparable_reason"
  | "coverage_pct"
  | "coverage_planned_trips"
  | "ci_low"
  | "ci_high"
  | "clamp_pct"
  | "delivered_missing"
  | "planned_trips"
> & { capability_tier?: CapabilityTier };

/** Half an on-time interval's width, rounded up to a tenth, so an interval
 *  the gate found wider than its limit never prints at the limit. The inner
 *  round drops float noise from the subtraction. */
function ciHalfWidth(low: number, high: number): number {
  return Math.ceil(Math.round(((high - low) / 2) * 1000) / 100) / 10;
}

/** Why coverage could not be measured, from what the figures say about the
 *  plan: the planned count coverage divides by is null for a feed whose
 *  trip_ids aren't the timetable's or a plan that could not be read, and 0
 *  where no timetable plans the days observed. */
function coverageUnknownWhy(a: GateFigures): "unreadable" | "unmatched" | "no_plan" | "no_plan_observed" {
  if (a.delivered_missing === "unreadable") return "unreadable";
  if (a.coverage_planned_trips == null) return "unmatched";
  return a.planned_trips === 0 ? "no_plan" : "no_plan_observed";
}

/** Whether coverage is unmeasurable because the agency's basic-tier feed
 *  can't be matched to the timetable at all, which its tier then states. */
export function coverageOutsideTier(a: GateFigures): boolean {
  return a.capability_tier === "basic" && coverageUnknownWhy(a) === "unmatched";
}

/** The first check of the comparability gate an agency fails, as the copy
 *  key that words it and the printed values that copy fills in (`why` is
 *  itself a copy key), so two verdicts with the same parts read the same in
 *  either language. Null for an agency the gate passes. */
export function unrankedReasonParts(
  a: GateFigures,
  limits: ComparabilityThresholds,
): { key: string; values: Record<string, string> } | null {
  switch (a.comparable_reason) {
    case null:
      return null;
    case "no_data":
      return { key: "network.unranked_reason.no_data", values: {} };
    case "coverage_unknown":
      if (coverageOutsideTier(a)) return { key: "capability.basic.unranked", values: {} };
      return {
        key: "network.unranked_reason.coverage_unknown",
        values: { why: `network.coverage_unknown_why.${coverageUnknownWhy(a)}` },
      };
    case "coverage_low":
      return {
        key: "network.unranked_reason.coverage_low",
        values: {
          pct: a.coverage_pct == null ? "—" : fixedTenth(a.coverage_pct),
          min: formatNumber(limits.min_coverage_pct),
        },
      };
    case "ci_wide":
      return {
        key: "network.unranked_reason.ci_wide",
        values: {
          half: a.ci_low == null || a.ci_high == null ? "—" : fixedTenth(ciHalfWidth(a.ci_low, a.ci_high)),
          max: formatNumber(limits.max_ci_half_width_pp),
        },
      };
    case "clamp_high":
      return {
        key: "network.unranked_reason.clamp_high",
        values: {
          pct: a.clamp_pct == null ? "—" : fixedTenth(a.clamp_pct),
          max: formatNumber(limits.max_clamp_pct),
        },
      };
  }
}

/** The first check of the comparability gate an agency fails, in words, with
 *  the figure and the threshold it missed, so the reader sees why without a
 *  lookup. Null for an agency the gate passes. */
export function unrankedReason(
  a: GateFigures,
  limits: ComparabilityThresholds,
  t: ReturnType<typeof useTranslation>["t"],
): string | null {
  const parts = unrankedReasonParts(a, limits);
  if (parts == null) return null;
  const { why, ...values } = parts.values;
  return t(parts.key, why == null ? values : { ...values, why: t(why) });
}
