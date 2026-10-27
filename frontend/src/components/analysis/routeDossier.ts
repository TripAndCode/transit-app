import { isoDaysBefore } from "../../api/scope";
import { REPORT_ROWS_MAX } from "../../api/hooks";
import type { OverviewConcentration, ReportResponse, RouteSegment, RouteShapeStop, RouteVariant } from "../../api/types";

const DAY_MS = 86_400_000;

/** How many days `from` through `to` spans, both included. */
export function windowDays(from: string, to: string): number {
  return Math.round((Date.parse(`${to}T00:00:00Z`) - Date.parse(`${from}T00:00:00Z`)) / DAY_MS) + 1;
}

/** The window of the same length that ends the day before `from`, so a
 *  comparison shares no day with the window it is compared with. */
export function previousWindow(from: string, to: string): { from: string; to: string } {
  return { from: isoDaysBefore(from, windowDays(from, to)), to: isoDaysBefore(from, 1) };
}

/** The smallest increase, in minutes, that still reads as one at the page's
 *  one decimal. */
const MIN_RISE_MIN = 0.05;

export type SegmentRise = { from: RouteShapeStop; to: RouteShapeStop; increaseMin: number };

/** The `n` segments where delay grows most, largest first and ties in stop
 *  order, among those whose two stops `stops` lists. */
export function largestRises(segments: RouteSegment[] | undefined, stops: RouteShapeStop[], n = 2): SegmentRise[] {
  const bySequence = new Map(stops.map((s) => [s.stop_sequence, s]));
  const rises: SegmentRise[] = [];
  for (const segment of segments ?? []) {
    const from = bySequence.get(segment.from_sequence);
    const to = bySequence.get(segment.to_sequence);
    if (from && to && segment.increase_min >= MIN_RISE_MIN) rises.push({ from, to, increaseMin: segment.increase_min });
  }
  rises.sort((a, b) => b.increaseMin - a.increaseMin || a.from.stop_sequence - b.from.stop_sequence);
  return rises.slice(0, n);
}

/** The most observed variant that runs between `current`'s end stops the
 *  other way, or null. A loop, which starts where it ends, has none. */
export function reverseVariant(variants: RouteVariant[], current: string | null): RouteVariant | null {
  const shown = variants.find((v) => v.shape_id === current);
  if (!shown || shown.first_stop_name === shown.last_stop_name) return null;
  return (
    variants.find(
      (v) => v.shape_id !== shown.shape_id && v.first_stop_name === shown.last_stop_name && v.last_stop_name === shown.first_stop_name,
    ) ?? null
  );
}

/** The stop the page opens on: the one with the largest delay, since most
 *  feeds read a trip's first stop as exactly on time. */
export function initialStop(stops: RouteShapeStop[]): RouteShapeStop | undefined {
  let best: RouteShapeStop | undefined;
  for (const s of stops) {
    if (s.avg_min != null && (best?.avg_min == null || s.avg_min > best.avg_min)) best = s;
  }
  return best ?? stops[0];
}

export type RouteStanding = {
  avgMin: number | null;
  /** Place among the agency's ranked routes, most delayed first. */
  rank: number | null;
  ranked: number | null;
  onTimePct: number | null;
  late5Pct: number | null;
  /** Share of the agency's total lateness, and its place by that share. */
  sharePct: number | null;
  shareRank: number | null;
};

type StandingSources = {
  ranking?: ReportResponse;
  onTime?: ReportResponse;
  worst?: ReportResponse;
  concentration?: OverviewConcentration;
};

function rowOf(report: ReportResponse | undefined, route: string): { row: unknown[]; index: number } | null {
  const rows = (report?.rows ?? []) as unknown[][];
  const index = rows.findIndex((r) => r[0] === route);
  return index < 0 ? null : { row: rows[index], index };
}

/** One route's figures as the agency's own route lists state them, so the
 *  route's page and the Routes lists cannot disagree. A list leaves out a
 *  route it does not rank (too few observations, a retired code, a feed that
 *  never reports delay), and the route then has no figure from it. The
 *  lateness share covers the routes the overview names, the largest few. */
export function routeStanding(route: string, sources: StandingSources): RouteStanding {
  const ranked = rowOf(sources.ranking, route);
  const onTime = rowOf(sources.onTime, route);
  const worst = rowOf(sources.worst, route);
  const worstComplete = (sources.worst?.rows.length ?? REPORT_ROWS_MAX) < REPORT_ROWS_MAX;
  const shareIndex = sources.concentration?.top_routes.findIndex((r) => r.route_code === route) ?? -1;
  const avg = ranked?.row[2] ?? onTime?.row[3];
  return {
    avgMin: avg == null ? null : Number(avg),
    rank: ranked ? ranked.index + 1 : null,
    ranked: ranked ? (sources.ranking?.rows_total ?? sources.ranking?.rows.length ?? null) : null,
    onTimePct: onTime ? Number(onTime.row[2]) : null,
    late5Pct: worst ? (Number(worst.row[2]) / Number(worst.row[4])) * 100 : onTime && worstComplete ? 0 : null,
    sharePct: shareIndex < 0 ? null : sources.concentration!.top_routes[shareIndex].share_pct,
    shareRank: shareIndex < 0 ? null : shareIndex + 1,
  };
}
