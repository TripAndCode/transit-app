import { REPORT_ROWS_MAX, useReport } from "../../api/hooks";
import type { Scope } from "../../api/scope";

/** Every route the lists rank, so a route's place among them is known. */
export const WHOLE_LIST = { limit: REPORT_ROWS_MAX } as const;

/** The agency's on-time list a route page reads the route's rate from. The
 *  summary strip shows the rate and the page reads the conditions it used;
 *  both go through here so they share one request. */
export function useAgencyOnTime(agencyId: number | null, ctx: Scope) {
  return useReport(agencyId, "on_time", { ...ctx, routes: [] }, WHOLE_LIST);
}
