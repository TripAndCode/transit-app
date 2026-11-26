import { BAND_ORDER, type Band } from "../../api/timeBands";
import type { TimetableFitSegment } from "./timetableFitApi";

/** The bands the route has a timed segment in, in clock order. */
export function fitBands(segments: TimetableFitSegment[]): Band[] {
  return BAND_ORDER.filter((band) => segments.some((s) => s.time_band === band));
}

/** The band shown first: the one with the most observed runs, the earlier in
 *  the day on a tie; null when no segment is timed. */
export function defaultBand(segments: TimetableFitSegment[]): Band | null {
  let best: Band | null = null;
  let bestRuns = -1;
  for (const band of fitBands(segments)) {
    const runs = segments.reduce((sum, s) => (s.time_band === band ? sum + s.samples : sum), 0);
    if (runs > bestRuns) {
      best = band;
      bestRuns = runs;
    }
  }
  return best;
}

/** A band's segments, in the route's order as the response gives them. */
export function bandRows(segments: TimetableFitSegment[], band: Band): TimetableFitSegment[] {
  return segments.filter((s) => s.time_band === band);
}

/** One scale for every strip of a band, so the bars compare down the table:
 *  the longest 85th-percentile or scheduled running time among the rows. */
export function stripScale(rows: TimetableFitSegment[]): number {
  return rows.reduce((max, s) => Math.max(max, s.run_p85_sec ?? 0, s.scheduled_run_sec ?? 0), 0);
}
