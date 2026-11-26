import { useQuery, type UseQueryResult } from "@tanstack/react-query";
import { apiGet } from "../../api/client";
import { scopeToQueryString, type Scope } from "../../api/scope";
import type { Band } from "../../api/timeBands";
import type { CoverageGapReason, DefinitionMeta } from "../../api/types";

/** One segment of the route in one time band. `padding_sec` is the scheduled
 *  running time minus the median: positive is slack, negative too tight.
 *  `time_band` is null for a segment the timetable gives no departure time;
 *  one with no observed run has no percentiles and no padding. */
export interface TimetableFitSegment {
  from_stop_id: string;
  from_stop_name: string | null;
  to_stop_id: string;
  to_stop_name: string | null;
  time_band: Band | null;
  to_stop_sequence: number;
  samples: number;
  run_p50_sec: number | null;
  run_p85_sec: number | null;
  scheduled_run_sec: number | null;
  padding_sec: number | null;
  hold_eligible: number;
  hold_rate: number | null;
}

/** `segments` in the route's order, then by band, all from the dates of
 *  `static_version_id`, the timetable in force on the range's last day. */
export interface TimetableFitResponse {
  available: boolean;
  unavailable_reason: CoverageGapReason | null;
  last_checked: string | null;
  value_basis: string;
  static_version_id: string | null;
  days: number;
  terminus_arrivals: number;
  terminus_early_rate: number | null;
  segments: TimetableFitSegment[];
  definition: DefinitionMeta;
  scope_applied: Record<string, boolean>;
}

export function timetableFitPath(agencyId: number, ctx: Scope, route: string): string {
  return `/api/${agencyId}/segments/timetable-fit?${scopeToQueryString(ctx)}&route=${encodeURIComponent(route)}`;
}

export function useTimetableFit(agencyId: number, ctx: Scope, route: string): UseQueryResult<TimetableFitResponse> {
  return useQuery({
    queryKey: ["timetable-fit", agencyId, scopeToQueryString(ctx), route],
    queryFn: ({ signal }) => apiGet<TimetableFitResponse>(timetableFitPath(agencyId, ctx, route), { signal }),
    staleTime: 5 * 60 * 1000,
    // A scope change keeps the route's last answer up while the next loads;
    // another agency's or route's segments are never shown as this one's.
    placeholderData: (previous, previousQuery) =>
      previousQuery?.queryKey[1] === agencyId && previousQuery.queryKey[3] === route ? previous : undefined,
  });
}
