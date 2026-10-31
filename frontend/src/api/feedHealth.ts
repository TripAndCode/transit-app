/** The share of the last seven analyzed days' readings that must have been
 *  set aside as implausible before the shell banner speaks up. A running
 *  feed sets aside stray readings, well under a thousandth of its volume,
 *  while a frozen or stale feed sets aside whole runs of them, so the line
 *  keeps the banner for a feed that actually stalled. Below it the count
 *  stays in the freshness panel. */
export const FEED_HEALTH_BANNER_MIN_SHARE = 0.001;

/** Whether the set-aside readings warrant the banner. Readings set aside
 *  against an unknown total cannot be judged negligible, so they do. */
export function feedHealthBannerDue(rawSamples: number, clampCount: number): boolean {
  if (clampCount <= 0) return false;
  if (rawSamples <= 0) return true;
  return clampCount / rawSamples >= FEED_HEALTH_BANNER_MIN_SHARE;
}
