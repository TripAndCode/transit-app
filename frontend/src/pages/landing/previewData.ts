// Static, deterministic fixtures for the landing page's scroll narrative
// (`ScrollNarrative.tsx`). None of this is fetched -- the narrative renders
// before sign-in, with no agency/session context to query real aggregates
// for, so every figure here is illustrative example data feeding the real
// chart components, not a snapshot of anything real.
//
// All three charts show one sample route: route 12 of the hero's fictional
// city, toward 北町, so a stop carries the same figure and the same name in
// every chart. Names are locale keys under `landing.hero_map.stops`.

import type { RouteShapeStop, TrendDay } from "../../api/types";
import type { StopEvidence } from "../../tabs/ask/stopEvidence";

export type SampleStopKey = "konan" | "shiyakusho" | "central" | "honmachi" | "toshokan" | "kitamachi";

type SampleStop = {
  key: SampleStopKey;
  /** Mean departure delay at the stop over the sample week, in minutes. */
  minutes: number;
  /** The same for the week before. */
  previousMinutes: number;
  samples: number;
};

// Delay builds stop by stop, with its largest step from 中央駅 to 本町 (the
// stretch the hero's selected trip lost time on); the week before stays
// comparatively flat, so the two lines visibly diverge as the route runs on.
const ROUTE_12: readonly SampleStop[] = [
  { key: "konan", minutes: 0.3, previousMinutes: 1.0, samples: 240 },
  { key: "shiyakusho", minutes: 0.8, previousMinutes: 1.1, samples: 238 },
  { key: "central", minutes: 1.4, previousMinutes: 1.0, samples: 231 },
  { key: "honmachi", minutes: 3.1, previousMinutes: 1.2, samples: 226 },
  { key: "toshokan", minutes: 3.5, previousMinutes: 1.1, samples: 219 },
  { key: "kitamachi", minutes: 3.9, previousMinutes: 1.3, samples: 214 },
];

/** How many stops the sample Ask answer returns; the real hotspot tool
 *  returns the route's worst few, ranked, never every stop. */
const EVIDENCE_STOPS = 4;

/** The route's stops for the stop chart, this week and the week before. */
export function sampleRouteStops(name: (key: SampleStopKey) => string): { current: RouteShapeStop[]; previous: RouteShapeStop[] } {
  const shape = (minutesOf: (stop: SampleStop) => number) =>
    ROUTE_12.map((stop, i) => ({
      stop_sequence: i + 1,
      stop_name: name(stop.key),
      stop_id: `R12-${i + 1}`,
      lon: 139.7 + i * 0.006,
      lat: 35.66 + i * 0.004,
      avg_min: minutesOf(stop),
      samples: stop.samples,
    }));
  return { current: shape((s) => s.minutes), previous: shape((s) => s.previousMinutes) };
}

/** The sample Ask answer: the route's stops with the highest delays this
 *  week, worst first, with the stop chart's own figures. */
export function sampleAskEvidence(name: (key: SampleStopKey) => string): StopEvidence[] {
  return ROUTE_12.map((stop, i) => ({ sequence: i + 1, name: name(stop.key), minutes: stop.minutes, samples: stop.samples }))
    .sort((a, b) => (b.minutes ?? 0) - (a.minutes ?? 0))
    .slice(0, EVIDENCE_STOPS);
}

/** The route's daily mean delay over two weeks: the calmer week before, then
 *  the sample week with its rough Thursday. Weekends run fewer trips. */
const DAILY: readonly (readonly [date: string, minutes: number, samples: number])[] = [
  ["2026-08-25", 1.2, 412],
  ["2026-08-26", 1.3, 405],
  ["2026-08-27", 1.0, 398],
  ["2026-08-28", 1.4, 420],
  ["2026-08-29", 0.8, 260],
  ["2026-08-30", 0.7, 245],
  ["2026-08-31", 1.2, 431],
  ["2026-09-01", 1.6, 400],
  ["2026-09-02", 2.2, 411],
  ["2026-09-03", 4.2, 418],
  ["2026-09-04", 2.4, 407],
  ["2026-09-05", 1.0, 250],
  ["2026-09-06", 0.9, 244],
  ["2026-09-07", 1.8, 397],
];

/** Trailing window of `TrendDay.avg_min_smoothed`, in days. */
const SMOOTHING_DAYS = 7;

/** Fed to the real `DailyChart`, with the trailing mean computed as the API
 *  computes it: sample-weighted over the last seven days. */
export const PREVIEW_DAILY_TREND: TrendDay[] = DAILY.map(([date, minutes, samples], i) => {
  const trailing = DAILY.slice(Math.max(0, i + 1 - SMOOTHING_DAYS), i + 1);
  const weight = trailing.reduce((sum, [, , n]) => sum + n, 0);
  const smoothed = trailing.reduce((sum, [, m, n]) => sum + m * n, 0) / weight;
  return { date, avg_min: minutes, avg_min_smoothed: Math.round(smoothed * 10) / 10, samples, top_offenders: [] };
});
