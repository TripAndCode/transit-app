import { keepPreviousData, useQuery, type UseQueryResult } from "@tanstack/react-query";
import { apiGet } from "../../api/client";
import type { DefinitionMeta } from "../../api/types";
import type { ScopeApplied } from "../scope/scopePhrases";

export const GRADES = ["A", "B", "C", "D", "F"] as const;
export type Grade = (typeof GRADES)[number];

export type ReportCardMetric = "on_time_pct" | "early_pct" | "ewt_min" | "bunching_pct" | "service_delivered_pct";

/** Why a route-month has no letter: a gate it failed, or `not_judged` with
 *  each figure it lacks in `missing`. */
export type NotGradedReason =
  | "no_data"
  | "zero_delay_feed"
  | "too_few_observations"
  | "coverage_unknown"
  | "coverage_low"
  | "ci_wide"
  | "not_judged";

/** One figure on a card: rates in percent, excess wait in minutes, to a tenth
 *  as judged. `letter` is its band, or for the service-delivered rate the best
 *  letter it allows; `graded` whether the route's class is graded on it. */
export interface SubScore {
  metric: ReportCardMetric;
  value: number | null;
  ci_low: number | null;
  ci_high: number | null;
  letter: Grade | null;
  graded: boolean;
  missing: string | null;
}

export interface RouteCard {
  route_code: string;
  month: string;
  frequency: "high" | "low";
  grade: Grade | null;
  not_graded: NotGradedReason | null;
  observations: number;
  floor: number;
  trips_observed: number | null;
  planned_trips: number | null;
  coverage_pct: number | null;
  on_time_ci_low: number | null;
  on_time_ci_high: number | null;
  missing: { metric: ReportCardMetric; reason: string }[];
  subscores: SubScore[];
  raise_to: { metric: ReportCardMetric; value: number; target: number }[];
}

export interface GradingRules {
  ewt_min_edges: number[];
  bunching_pct_edges: number[];
  on_time_pct_edges: number[];
  early_pct_edges: number[];
  truancy_cap_pct: number;
  truancy_fail_pct: number;
  min_observations: number;
}

export interface RouteReportCardResponse {
  month: string;
  latest_month: string;
  /** Null when the route was neither observed nor planned that month. */
  card: RouteCard | null;
  /** The month and the ones before it, oldest first. */
  history: { month: string; grade: Grade | null; not_graded: NotGradedReason | null }[];
  rules: GradingRules;
  definition: DefinitionMeta;
  /** Every field false: a card reads its own month, none of the shared scope. */
  scope_applied?: ScopeApplied;
}

export interface ReportCardListResponse {
  month: string;
  latest_month: string;
  cards: RouteCard[];
  rules: GradingRules;
  definition: DefinitionMeta;
  /** Every field false: a card reads its own month, none of the shared scope. */
  scope_applied?: ScopeApplied;
}

function monthQuery(month: string | null): string {
  return month ? `?month=${encodeURIComponent(month)}` : "";
}

export function reportCardListPath(agencyId: number, month: string | null, csv = false): string {
  const params = new URLSearchParams();
  if (month) params.set("month", month);
  if (csv) params.set("format", "csv");
  const query = params.toString();
  return `/api/${agencyId}/report-cards${query ? `?${query}` : ""}`;
}

/** One route's card for *month*, the newest closed month when null. */
export function useRouteReportCard(
  agencyId: number | null,
  route: string | null,
  month: string | null,
): UseQueryResult<RouteReportCardResponse> {
  return useQuery({
    queryKey: ["route-report-card", agencyId, route, month],
    queryFn: ({ signal }) =>
      apiGet<RouteReportCardResponse>(
        `/api/${agencyId}/routes/${encodeURIComponent(route ?? "")}/report-card${monthQuery(month)}`,
        { signal },
      ),
    enabled: agencyId != null && route != null,
    staleTime: 5 * 60 * 1000,
    placeholderData: keepPreviousData,
  });
}

/** Every route's card for *month*, the newest closed month when null. */
export function useReportCards(agencyId: number | null, month: string | null): UseQueryResult<ReportCardListResponse> {
  return useQuery({
    queryKey: ["report-cards", agencyId, month],
    queryFn: ({ signal }) => apiGet<ReportCardListResponse>(reportCardListPath(agencyId as number, month), { signal }),
    enabled: agencyId != null,
    staleTime: 5 * 60 * 1000,
    placeholderData: keepPreviousData,
  });
}
