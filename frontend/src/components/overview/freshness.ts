import { CLOCK_SKEW_ALLOWANCE_MS } from "../../tabs/map/liveRowsFilter";

/** The hero's freshness dot breathes only while the feed is this fresh: a
 *  pulse on a stale feed would claim a liveness the data does not have. */
export const BREATH_WINDOW_MS = 2 * 60_000;

/** True while `latestIso` is a parseable instant less than BREATH_WINDOW_MS
 *  old. A report slightly ahead of this clock is fresh by the same skew
 *  allowance Live keeps; one further ahead is not evidence of liveness. */
export function isBreathing(latestIso: string | null | undefined, nowMs: number): boolean {
  if (!latestIso) return false;
  const age = nowMs - new Date(latestIso).getTime();
  return Number.isFinite(age) && age >= CLOCK_SKEW_ALLOWANCE_MS && age < BREATH_WINDOW_MS;
}
