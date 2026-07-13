/**
 * Pure grouping helper for the Overview "routes to check" list: partitions
 * routes into the same absolute-delay severity bands used by the Map legend
 * and delayColor() — NOT Live tab's baseline-deviation buckets (a different
 * question: "how bad, absolutely, over this range" vs. "what changed today").
 * Overview's top_delayed payload carries no baseline comparison at all.
 */
import type { OverviewTopDelayedRoute } from "../api/types";

type Band = "severe" | "moderate" | "mild" | "ok";

const BAND_ORDER: { band: Band; labelKey: string; test: (m: number) => boolean }[] = [
  { band: "severe", labelKey: "map.legend.band_gt_5", test: (m) => m >= 5 },
  { band: "moderate", labelKey: "map.legend.band_3_5", test: (m) => m >= 3 && m < 5 },
  { band: "mild", labelKey: "map.legend.band_1_5_3", test: (m) => m >= 1.5 && m < 3 },
  { band: "ok", labelKey: "map.legend.band_lt_1_5", test: (m) => m < 1.5 },
];

export type BandGroup = { band: Band; labelKey: string; routes: OverviewTopDelayedRoute[] };

export function groupBySeverityBand(routes: OverviewTopDelayedRoute[]): BandGroup[] {
  return BAND_ORDER.map(({ band, labelKey, test }) => ({
    band,
    labelKey,
    routes: routes.filter((r) => test(r.avg_min)).sort((a, b) => b.avg_min - a.avg_min),
  })).filter((g) => g.routes.length > 0);
}
