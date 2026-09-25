import type { TimeBand } from "../../api/rangeContext";
import type { RouteTrip } from "../../api/types";
import { delayColor } from "../../styles/tokens";

/** One rung of the y axis: the route's stop sequence, top to bottom. */
export type MareyStop = { stop_sequence: number; stop_name: string };

/** Seconds since the service day's 00:00, the same origin the API's
 *  `scheduled_sec`/`observed_sec` use — so a post-midnight continuation
 *  (25:30) sits after the evening rather than back at the start of the day. */
export type TimeWindow = { startSec: number; endSec: number };

/** The drawable rectangle inside an SVG viewBox, axis gutters excluded. */
export type Plot = { left: number; top: number; width: number; height: number };

export type StopRibbonSegment = {
  stop_sequence: number;
  stop_name: string;
  /** Mean delay over the trips in the window; null when none was observed. */
  delay_sec: number | null;
  samples: number;
};

/** Opacity the un-hovered trips drop to. Low enough that one line reads as
 *  isolated, high enough that the others stay as context rather than vanish. */
export const MUTED_OPACITY = 0.14;

/** Window drawn when no time band is selected. A whole service day of trips
 *  is unreadable at any sane width, and the morning commute is where a
 *  time–distance diagram earns its place. */
export const DEFAULT_MAREY_WINDOW: TimeWindow = { startSec: 6 * 3600, endSec: 10 * 3600 };

const HOUR_SEC = 3600;

/** Fewest stop observations an hour needs before it can be called the peak —
 *  one late bus is an anecdote, not a period of the day. */
const MIN_PEAK_OBSERVATIONS = 3;

/** Mirrors `_TIME_BAND_RANGES` in api/range.py, which is the canonical
 *  definition; the server filters the rows, this only positions them. */
const BAND_WINDOWS: Record<Exclude<TimeBand, "all">, TimeWindow> = {
  morning: { startSec: 5 * HOUR_SEC, endSec: 9 * HOUR_SEC },
  forenoon: { startSec: 9 * HOUR_SEC, endSec: 12 * HOUR_SEC },
  noon: { startSec: 12 * HOUR_SEC, endSec: 14 * HOUR_SEC },
  afternoon: { startSec: 14 * HOUR_SEC, endSec: 17 * HOUR_SEC },
  evening: { startSec: 17 * HOUR_SEC, endSec: 20 * HOUR_SEC },
  night: { startSec: 20 * HOUR_SEC, endSec: 24 * HOUR_SEC },
  late_night: { startSec: 0, endSec: 5 * HOUR_SEC },
};

export function timeWindowForBand(band: TimeBand): TimeWindow {
  return band === "all" ? DEFAULT_MAREY_WINDOW : BAND_WINDOWS[band];
}

/** Horizontal position of a time. Deliberately unclamped: a time outside the
 *  window lands outside the plot and is clipped there, because folding it onto
 *  an edge would draw a vertical line no bus ran. */
export function timeToX(sec: number, viewWindow: TimeWindow, plot: Plot): number {
  const span = viewWindow.endSec - viewWindow.startSec;
  if (span <= 0) return plot.left;
  return plot.left + ((sec - viewWindow.startSec) / span) * plot.width;
}

/** Vertical position of a stop, or null when the route's axis has no such
 *  stop — a shape variant can report a sequence the representative path
 *  never visits, and drawing it at y=0 would invent a leg. */
export function seqToY(sequence: number, axis: MareyStop[], plot: Plot): number | null {
  const index = axis.findIndex((s) => s.stop_sequence === sequence);
  if (index < 0) return null;
  if (axis.length < 2) return plot.top;
  return plot.top + (index / (axis.length - 1)) * plot.height;
}

/** The shared delay ramp, fed seconds instead of minutes. */
export function segmentColor(delaySec: number): string {
  return delayColor(delaySec / 60);
}

/** Scheduled departure of a trip: the first stop that has a placeable time. */
export function tripDeparture(trip: RouteTrip): number | null {
  for (const stop of trip.stops) if (stop.scheduled_sec != null) return stop.scheduled_sec;
  return null;
}

/** Delay at the last stop the trip was observed at — how much of the journey's
 *  lateness actually reached the far end of the line. */
export function tripTerminalDelay(trip: RouteTrip): number | null {
  return trip.stops.length ? trip.stops[trip.stops.length - 1].delay_sec : null;
}

export function tripsInWindow(trips: RouteTrip[], viewWindow: TimeWindow): RouteTrip[] {
  return trips.filter((trip) => {
    const departure = tripDeparture(trip);
    return departure != null && departure >= viewWindow.startSec && departure < viewWindow.endSec;
  });
}

/** The hour inside `viewWindow` whose trips lost the most time on average, or null
 *  when no hour carries enough observations to make that claim. */
export function peakWindow(trips: RouteTrip[], viewWindow: TimeWindow): TimeWindow | null {
  const byHour = new Map<number, { total: number; count: number }>();
  for (const trip of tripsInWindow(trips, viewWindow)) {
    const hour = Math.floor(tripDeparture(trip)! / HOUR_SEC);
    const bucket = byHour.get(hour) ?? { total: 0, count: 0 };
    for (const stop of trip.stops) {
      bucket.total += stop.delay_sec;
      bucket.count += 1;
    }
    byHour.set(hour, bucket);
  }
  let best: { hour: number; mean: number } | null = null;
  for (const [hour, { total, count }] of byHour) {
    if (count < MIN_PEAK_OBSERVATIONS) continue;
    const mean = total / count;
    if (best === null || mean > best.mean) best = { hour, mean };
  }
  return best === null ? null : { startSec: best.hour * HOUR_SEC, endSec: (best.hour + 1) * HOUR_SEC };
}

/** Mean delay per stop across the trips departing inside `viewWindow`.
 *
 *  Every stop on the axis gets a segment, observed or not: the ribbon is the
 *  route's geography, so a stop with no data must read as a gap rather than
 *  shorten the line and misplace every stop after it.
 */
export function ribbonSegments(trips: RouteTrip[], axis: MareyStop[], viewWindow: TimeWindow): StopRibbonSegment[] {
  const totals = new Map<number, { total: number; count: number }>();
  for (const trip of tripsInWindow(trips, viewWindow)) {
    for (const stop of trip.stops) {
      const bucket = totals.get(stop.stop_sequence) ?? { total: 0, count: 0 };
      bucket.total += stop.delay_sec;
      bucket.count += 1;
      totals.set(stop.stop_sequence, bucket);
    }
  }
  return axis.map((stop) => {
    const bucket = totals.get(stop.stop_sequence);
    return {
      stop_sequence: stop.stop_sequence,
      stop_name: stop.stop_name,
      delay_sec: bucket && bucket.count ? Math.round(bucket.total / bucket.count) : null,
      samples: bucket?.count ?? 0,
    };
  });
}

/** `HH:MM` for a seconds-since-service-day-start value. Hours past 24 stay
 *  unwrapped — 25:30 is a real GTFS departure time, and folding it to 01:30
 *  would move the trip to the wrong end of the axis. */
export function formatClock(sec: number): string {
  const hours = Math.floor(sec / HOUR_SEC);
  const minutes = Math.floor((sec - hours * HOUR_SEC) / 60);
  return `${String(hours).padStart(2, "0")}:${String(minutes).padStart(2, "0")}`;
}

/** Whole-hour gridline positions covering `viewWindow`. */
export function hourTicks(viewWindow: TimeWindow): number[] {
  const ticks: number[] = [];
  for (let sec = Math.ceil(viewWindow.startSec / HOUR_SEC) * HOUR_SEC; sec <= viewWindow.endSec; sec += HOUR_SEC) {
    ticks.push(sec);
  }
  return ticks;
}
