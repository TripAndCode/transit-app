import type { RouteShapeStop, RouteTrip } from "../../api/types";
import type { MareyStop, TimeWindow } from "./mareyLayout";

export type TripSpan = { startSec: number; endSec: number };
export type ScrubPosition = { trip_id: string; fromSeq: number; toSeq: number; f: number; axisFraction: number; delaySec: number };

type Placed = { seq: number; sec: number; delaySec: number };

/** The trip's stops that can be placed on the clock, in sequence order --
 *  the observed time where the feed reported one, the schedule otherwise. */
function placed(trip: RouteTrip): Placed[] {
  return [...trip.stops]
    .sort((a, b) => a.stop_sequence - b.stop_sequence)
    .flatMap((s) => {
      const sec = s.observed_sec ?? s.scheduled_sec;
      return sec == null ? [] : [{ seq: s.stop_sequence, sec, delaySec: s.delay_sec }];
    });
}

export function tripSpan(trip: RouteTrip): TripSpan | null {
  const stops = placed(trip);
  if (stops.length === 0) return null;
  return { startSec: stops[0].sec, endSec: stops[stops.length - 1].sec };
}

export function tripsCrossing(trips: RouteTrip[], sec: number): RouteTrip[] {
  return trips.filter((trip) => {
    const span = tripSpan(trip);
    return span != null && sec >= span.startSec && sec <= span.endSec;
  });
}

/** Where each crossing trip is at `sec`, as the pair of axis stops it is
 *  between and the fraction between them. A leg whose stop the axis never
 *  visits is skipped: drawing it would invent a position on a line the
 *  route does not run. */
export function positionsAt(trips: RouteTrip[], sec: number, axis: MareyStop[]): ScrubPosition[] {
  const index = new Map(axis.map((s, i) => [s.stop_sequence, i]));
  const denom = Math.max(1, axis.length - 1);
  const out: ScrubPosition[] = [];
  for (const trip of tripsCrossing(trips, sec)) {
    const stops = placed(trip);
    for (let i = 1; i < stops.length; i += 1) {
      const a = stops[i - 1];
      const b = stops[i];
      if (sec < a.sec || sec > b.sec) continue;
      const ia = index.get(a.seq);
      const ib = index.get(b.seq);
      if (ia == null || ib == null) break;
      const f = b.sec === a.sec ? 1 : (sec - a.sec) / (b.sec - a.sec);
      out.push({ trip_id: trip.trip_id, fromSeq: a.seq, toSeq: b.seq, f, axisFraction: (ia + (ib - ia) * f) / denom, delaySec: b.delaySec });
      break;
    }
  }
  return out;
}

export function scrubBounds(viewWindow: TimeWindow): { min: number; max: number; step: number } {
  return { min: Math.ceil(viewWindow.startSec / 60) * 60, max: Math.floor(viewWindow.endSec / 60) * 60, step: 60 };
}

export function defaultScrubSec(viewWindow: TimeWindow, peak: TimeWindow | null): number {
  return peak?.startSec ?? viewWindow.startSec;
}

/** The position on the route's shape: a straight interpolation between the
 *  two stops' coordinates. Straight, not along the shape polyline -- the
 *  trips response carries no distance-along-shape, and a point a few metres
 *  off a curve is honest about that; a point snapped to the curve would not be. */
export function interpolateLngLat(stops: RouteShapeStop[], pos: ScrubPosition): [number, number] | null {
  const a = stops.find((s) => s.stop_sequence === pos.fromSeq);
  const b = stops.find((s) => s.stop_sequence === pos.toSeq);
  if (!a || !b || ![a.lon, a.lat, b.lon, b.lat].every(Number.isFinite)) return null;
  return [a.lon + (b.lon - a.lon) * pos.f, a.lat + (b.lat - a.lat) * pos.f];
}
