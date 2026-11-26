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

/** A band's segments as runs of consecutive stops, each a direction or
 *  branch of the route in stop order. A segment is keyed by its stop pair
 *  alone, and opposite directions stop at different poles, so following each
 *  segment's last stop to the next segment's first keeps a direction together
 *  where the stop sequence would interleave them. A run starts at a segment
 *  no other leads into, rather than at the lowest sequence, which a trip
 *  starting mid-route lowers. A loop, which has no such segment, starts at its
 *  lowest sequence; at a branch the lower sequence continues the run. */
export function bandRuns(segments: TimetableFitSegment[], band: Band): TimetableFitSegment[][] {
  const rows = segments.filter((s) => s.time_band === band);
  const entered = new Set(rows.map((s) => s.to_stop_id));
  const visited = new Set<TimetableFitSegment>();
  const runs: TimetableFitSegment[][] = [];
  for (;;) {
    const left = rows.filter((s) => !visited.has(s));
    if (left.length === 0) return runs;
    const run: TimetableFitSegment[] = [];
    let next: TimetableFitSegment | undefined = left.find((s) => !entered.has(s.from_stop_id)) ?? left[0];
    while (next) {
      visited.add(next);
      run.push(next);
      const stop: string = next.to_stop_id;
      next = rows.find((s) => !visited.has(s) && s.from_stop_id === stop);
    }
    runs.push(run);
  }
}

/** One scale for every strip of a band, so the bars compare down the table:
 *  the longest 85th-percentile or scheduled running time among the rows. */
export function stripScale(rows: TimetableFitSegment[]): number {
  return rows.reduce((max, s) => Math.max(max, s.run_p85_sec ?? 0, s.scheduled_run_sec ?? 0), 0);
}
