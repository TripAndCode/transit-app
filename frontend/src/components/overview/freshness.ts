/** The hero's freshness dot breathes only while the feed is this fresh: a
 *  pulse on a stale feed would claim a liveness the data does not have. */
export const BREATH_WINDOW_MS = 2 * 60_000;

/** True while `latestIso` is a parseable instant at most just under
 *  BREATH_WINDOW_MS before `nowMs`. A timestamp from the future (clock skew)
 *  is not evidence of liveness either, so it does not breathe. */
export function isBreathing(latestIso: string | null | undefined, nowMs: number): boolean {
  if (!latestIso) return false;
  const age = nowMs - new Date(latestIso).getTime();
  return Number.isFinite(age) && age >= 0 && age < BREATH_WINDOW_MS;
}
