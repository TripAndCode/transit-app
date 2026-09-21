import type { LiveTrip } from "../../api/types";
import { MAX_REPORT_AGE_MS } from "./liveRowsFilter";

// Age at which the header freshness badge moves from "normal" to "delayed"
// (see freshnessFor in MapTab.tsx); MAX_REPORT_AGE_MS is both the badge's
// "delayed" -> "stale" boundary and filterLiveRows' exit age, so the two
// stay in lockstep by construction.
const FRESHNESS_DELAYED_AGE_MS = 2 * 60_000;

/**
 * Earliest timestamp (ms since epoch) after `now` at which some row's age
 * crosses a staleness boundary -- either leaving the live-rows age window
 * (MAX_REPORT_AGE_MS) or moving the header freshness badge from "normal" to
 * "delayed" (FRESHNESS_DELAYED_AGE_MS). Returns null when no row has a
 * pending boundary (no rows, or every boundary already behind `now`).
 *
 * A pure function of (rows, now) so MapTab can schedule a single timeout to
 * this exact instant instead of polling on a fixed interval, and so the
 * scheduling logic is testable without a timer or a mounted component.
 */
export function nextBoundaryMs(rows: LiveTrip[], now: number): number | null {
  let next: number | null = null;
  for (const row of rows) {
    const capturedMs = Date.parse(row.captured_at);
    if (!Number.isFinite(capturedMs)) continue;
    for (const ageMs of [FRESHNESS_DELAYED_AGE_MS, MAX_REPORT_AGE_MS]) {
      const boundary = capturedMs + ageMs;
      if (boundary > now && (next === null || boundary < next)) next = boundary;
    }
  }
  return next;
}
