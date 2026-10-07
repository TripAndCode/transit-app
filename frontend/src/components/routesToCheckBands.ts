/**
 * Pure grouping helper for the Overview "routes to check" list: partitions
 * routes into the same absolute-delay severity bands used by the Map legend
 * and delayColor() (via the shared delayBand() classifier, so the two can
 * never drift apart) — NOT Live tab's baseline-deviation buckets (a
 * different question: "how bad, absolutely, over this range" vs. "what
 * changed today"). Overview's top_delayed payload carries no baseline
 * comparison at all.
 *
 * The backend's top-delayed query has no minimum-delay floor (it's simply
 * "worst N routes by avg_min"), so a healthy agency's worst-5 can include
 * routes that are, by this app's own color ramp, fine (<1.5 min). Excluding
 * the "ok" band here means "Routes to check now" never shows a route this
 * app's own severity ramp considers fine — showing one there would
 * contradict the section's whole purpose.
 */
import { delayBand } from "../styles/tokens";
import type { OverviewTopDelayedRoute } from "../api/types";

const BAND_ORDER = ["severe", "moderate", "mild"] as const;

type Band = (typeof BAND_ORDER)[number];
type BandGroup = { band: Band; routes: OverviewTopDelayedRoute[] };

export function groupBySeverityBand(routes: OverviewTopDelayedRoute[]): BandGroup[] {
  return BAND_ORDER.map((band) => ({
    band,
    routes: routes.filter((r) => delayBand(r.avg_min) === band).sort((a, b) => b.avg_min - a.avg_min),
  })).filter((g) => g.routes.length > 0);
}
