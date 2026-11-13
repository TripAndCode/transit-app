import { delayBand } from "../../styles/tokens";
import type { ReplayResponse, ReplayTrip, StopBounds } from "../../api/types";

/** Mirrors `api.routers.replay.MAX_REPLAY_WINDOW_HOURS`: the width of one
 *  fetched window, and the step the window grid advances by. Not exported:
 *  every other module reaches it only through `windowStartForHour` or
 *  `desiredWindowKeys`. */
const WINDOW_WIDTH_HOURS = 3;

/** Mirrors `pipeline.service_day.SERVICE_DAY_CUTOVER_HOURS`: the hour a new
 *  service day is considered to start, so the scrubber's own span (one
 *  cutover to the next) matches the clock GTFS schedules already use. Not
 *  exported: every other module reaches it only through
 *  `scrubberStartHour` or `SCRUBBER_END_HOUR`. */
const SCRUBBER_DEFAULT_START_HOUR = 4;
export const SCRUBBER_END_HOUR = SCRUBBER_DEFAULT_START_HOUR + 24;

/** The scrubber always covers the service day's usual span, pulled earlier
 *  when the day's own first observation starts before it (an agency whose
 *  earliest run is before 04:00 would otherwise have no track to land on). */
export function scrubberStartHour(defaultFromHour: number | null): number {
  return defaultFromHour == null
    ? SCRUBBER_DEFAULT_START_HOUR
    : Math.min(SCRUBBER_DEFAULT_START_HOUR, defaultFromHour);
}

/** One stop event resolved against its own response's `stops` arrays, so an
 *  index from a different window's response can never leak in here. */
type ResolvedStopEvent = {
  t: number;
  delaySec: number;
  lat: number;
  lon: number;
  stopId: string;
};

export type ResolvedTrip = {
  tripId: string;
  routeCode: string | null;
  /** Time-ordered, non-decreasing in `t` (see `monotoneEnvelope`). */
  events: ResolvedStopEvent[];
};

type BusTownSegment = {
  tripId: string;
  routeCode: string | null;
  t0: number;
  t1: number;
  lat0: number;
  lon0: number;
  lat1: number;
  lon1: number;
  /** The segment's starting event's delay: colour follows the same
   *  carried-forward step function the backend reports, not a continuous
   *  interpolation, since a delay reading has no meaning between stops. */
  delaySec: number;
};

export type BusTownBus = {
  tripId: string;
  routeCode: string | null;
  lat: number;
  lon: number;
  delayMin: number;
};

export type StopPoint = { id: string; lat: number; lon: number };
export type StopPair = { lat0: number; lon0: number; lat1: number; lon1: number };

function resolveWindowTrips(replayWindow: ReplayResponse): ResolvedTrip[] {
  const { stops } = replayWindow;
  return replayWindow.trips.map((trip: ReplayTrip) => {
    const events: ResolvedStopEvent[] = [];
    for (let i = 0; i < trip.stop_idx.length; i++) {
      const idx = trip.stop_idx[i];
      const lat = stops.lat[idx];
      const lon = stops.lon[idx];
      if (lat == null || lon == null) continue;
      events.push({ t: trip.t[i], delaySec: trip.delay[i], lat, lon, stopId: stops.id[idx] });
    }
    return { tripId: trip.trip_id, routeCode: trip.route_code, events };
  });
}

/** Clamps a run of events, kept in their original stop-sequence order, to be
 *  non-decreasing in `t`. Events are never re-sorted by `t`: the sequence a
 *  trip visits its stops is physical fact, while an individual observed `t`
 *  can wobble below a now-reported stop's own earlier value. Clamping each
 *  one up to the running maximum preserves the real visiting order and
 *  guarantees a trip has at most one drawable position at any second. */
function monotoneEnvelope(events: readonly ResolvedStopEvent[]): ResolvedStopEvent[] {
  let runningMax = -Infinity;
  return events.map((event) => {
    const t = Math.max(event.t, runningMax);
    runningMax = t;
    return t === event.t ? event : { ...event, t };
  });
}

/** Merges every loaded window's trips by `trip_id`, resolving each window's
 *  stop indices against that window's own `stops` before merging — an index
 *  only means something inside the response it came from. A trip whose
 *  scheduled stops straddle a window boundary has its events split across
 *  two windows; merging recombines them into one continuous run. */
export function mergeReplayWindows(windows: readonly ReplayResponse[]): Map<string, ResolvedTrip> {
  const byTrip = new Map<string, { routeCode: string | null; events: ResolvedStopEvent[] }>();
  const ordered = [...windows].sort((a, b) => (a.from_hour ?? 0) - (b.from_hour ?? 0));
  for (const replayWindow of ordered) {
    for (const trip of resolveWindowTrips(replayWindow)) {
      const existing = byTrip.get(trip.tripId);
      if (existing) {
        existing.events.push(...trip.events);
        if (trip.routeCode != null) existing.routeCode = trip.routeCode;
      } else {
        byTrip.set(trip.tripId, { routeCode: trip.routeCode, events: [...trip.events] });
      }
    }
  }
  const result = new Map<string, ResolvedTrip>();
  for (const [tripId, { routeCode, events }] of byTrip) {
    result.set(tripId, { tripId, routeCode, events: monotoneEnvelope(events) });
  }
  return result;
}

function buildSegments(trip: ResolvedTrip): BusTownSegment[] {
  const segments: BusTownSegment[] = [];
  for (let i = 0; i + 1 < trip.events.length; i++) {
    const a = trip.events[i];
    const b = trip.events[i + 1];
    if (b.t <= a.t) continue; // zero-length after the monotone clamp: nothing to glide between
    segments.push({
      tripId: trip.tripId,
      routeCode: trip.routeCode,
      t0: a.t,
      t1: b.t,
      lat0: a.lat,
      lon0: a.lon,
      lat1: b.lat,
      lon1: b.lon,
      delaySec: a.delaySec,
    });
  }
  return segments;
}

/** `trip_id` -> its drawable segments, in order. A trip with fewer than two
 *  usable (coordinate-resolved) events has no segments at all, and is never
 *  drawn — matching the contract's "not drawn before its first or after its
 *  last observed event". */
export type BusTownModel = ReadonlyMap<string, readonly BusTownSegment[]>;

export function buildBusTownModel(trips: ReadonlyMap<string, ResolvedTrip>): BusTownModel {
  const model = new Map<string, readonly BusTownSegment[]>();
  for (const [tripId, trip] of trips) {
    const segments = buildSegments(trip);
    if (segments.length > 0) model.set(tripId, segments);
  }
  return model;
}

/** Every bus estimated to be running at `sec`: one entry per trip whose
 *  segments bracket it, linearly interpolated between its two bracketing
 *  stop events. */
export function positionsAt(model: BusTownModel, sec: number): BusTownBus[] {
  const buses: BusTownBus[] = [];
  for (const segments of model.values()) {
    if (segments.length === 0 || sec < segments[0].t0 || sec > segments[segments.length - 1].t1) continue;
    const seg = segments.find((s) => sec >= s.t0 && sec <= s.t1);
    if (!seg) continue;
    const span = seg.t1 - seg.t0;
    const frac = span === 0 ? 0 : (sec - seg.t0) / span;
    buses.push({
      tripId: seg.tripId,
      routeCode: seg.routeCode,
      lat: seg.lat0 + (seg.lat1 - seg.lat0) * frac,
      lon: seg.lon0 + (seg.lon1 - seg.lon0) * frac,
      delayMin: seg.delaySec / 60,
    });
  }
  return buses;
}

/** "On time" is exactly `delayBand`'s "ok" tier (< 1.5 min = 90s) — the same
 *  classifier the colour ramp and its legend use, so the count on the card
 *  can never disagree with what the canvas is showing. */
export function onTimeCount(buses: readonly BusTownBus[]): { onTime: number; total: number } {
  return { onTime: buses.filter((b) => delayBand(b.delayMin) === "ok").length, total: buses.length };
}

export type SlowestRoute = { routeCode: string; name: string; meanDelayMin: number };

/** The route with the worst mean delay over every event in the loaded
 *  windows (not only the buses currently drawn), so the figure doesn't
 *  flicker between routes as buses enter and leave the screen. Ties break on
 *  route_code ascending, the only ordering the data gives no other claim to. */
export function slowestRoute(
  trips: ReadonlyMap<string, ResolvedTrip>,
  routeNames: Readonly<Record<string, string>>,
): SlowestRoute | null {
  const sums = new Map<string, { sum: number; count: number }>();
  for (const trip of trips.values()) {
    if (trip.routeCode == null) continue;
    const bucket = sums.get(trip.routeCode) ?? { sum: 0, count: 0 };
    for (const event of trip.events) {
      bucket.sum += event.delaySec;
      bucket.count += 1;
    }
    sums.set(trip.routeCode, bucket);
  }
  let best: { routeCode: string; meanSec: number } | null = null;
  for (const routeCode of [...sums.keys()].sort()) {
    const { sum, count } = sums.get(routeCode)!;
    if (count === 0) continue;
    const meanSec = sum / count;
    if (!best || meanSec > best.meanSec) best = { routeCode, meanSec };
  }
  if (!best) return null;
  return { routeCode: best.routeCode, name: routeNames[best.routeCode] ?? best.routeCode, meanDelayMin: best.meanSec / 60 };
}

export function stopPointsFromWindows(windows: readonly ReplayResponse[]): StopPoint[] {
  const seen = new Map<string, StopPoint>();
  for (const replayWindow of windows) {
    const { stops } = replayWindow;
    for (let i = 0; i < stops.id.length; i++) {
      const lat = stops.lat[i];
      const lon = stops.lon[i];
      if (lat == null || lon == null || seen.has(stops.id[i])) continue;
      seen.set(stops.id[i], { id: stops.id[i], lat, lon });
    }
  }
  return [...seen.values()];
}

/** Every consecutive stop pair any trip visits, deduped regardless of
 *  direction, for the schematic's faint background lines. */
export function stopPairsFromWindows(windows: readonly ReplayResponse[]): StopPair[] {
  const seen = new Set<string>();
  const pairs: StopPair[] = [];
  for (const replayWindow of windows) {
    const { stops } = replayWindow;
    for (const trip of replayWindow.trips) {
      for (let i = 0; i + 1 < trip.stop_idx.length; i++) {
        const ia = trip.stop_idx[i];
        const ib = trip.stop_idx[i + 1];
        const latA = stops.lat[ia];
        const lonA = stops.lon[ia];
        const latB = stops.lat[ib];
        const lonB = stops.lon[ib];
        if (latA == null || lonA == null || latB == null || lonB == null) continue;
        const idA = stops.id[ia];
        const idB = stops.id[ib];
        if (idA === idB) continue;
        const key = idA < idB ? `${idA}|${idB}` : `${idB}|${idA}`;
        if (seen.has(key)) continue;
        seen.add(key);
        pairs.push({ lat0: latA, lon0: lonA, lat1: latB, lon1: lonB });
      }
    }
  }
  return pairs;
}

export type Projection = (lat: number, lon: number) => { x: number; y: number };

/** An equirectangular projection fit to `bounds`, padded and centred inside
 *  `width`x`height`. Longitude is scaled by the bounding box's own centre
 *  latitude's cosine, so a degree of longitude and a degree of latitude cover
 *  the same screen distance near that latitude — close enough for a schematic
 *  spanning one agency's service area. */
export function fitProjection(bounds: StopBounds, width: number, height: number, padding = 24): Projection {
  const [west, south, east, north] = bounds;
  const centerLat = (south + north) / 2;
  const cos = Math.max(Math.cos((centerLat * Math.PI) / 180), 0.1);
  const lonSpan = Math.max((east - west) * cos, 1e-6);
  const latSpan = Math.max(north - south, 1e-6);
  const innerW = Math.max(width - padding * 2, 1);
  const innerH = Math.max(height - padding * 2, 1);
  const scale = Math.min(innerW / lonSpan, innerH / latSpan);
  const offsetX = padding + (innerW - lonSpan * scale) / 2;
  const offsetY = padding + (innerH - latSpan * scale) / 2;
  return (lat, lon) => ({
    x: offsetX + (lon - west) * cos * scale,
    y: offsetY + (north - lat) * scale,
  });
}

export function boundsFromPoints(points: readonly { lat: number; lon: number }[]): StopBounds | null {
  if (points.length === 0) return null;
  let west = Infinity;
  let east = -Infinity;
  let south = Infinity;
  let north = -Infinity;
  for (const p of points) {
    west = Math.min(west, p.lon);
    east = Math.max(east, p.lon);
    south = Math.min(south, p.lat);
    north = Math.max(north, p.lat);
  }
  return [west, south, east, north];
}

/** The 3-hour grid window `hour` falls in, anchored on `originHour` (the
 *  day's own default window start) rather than a fixed clock hour — an
 *  origin-anchored grid never duplicates a (trip, stop) event between two
 *  adjacent windows, which a fixed grid that didn't line up with the day's
 *  own default window would. */
export function windowStartForHour(hour: number, originHour: number): number {
  return originHour + WINDOW_WIDTH_HOURS * Math.floor((hour - originHour) / WINDOW_WIDTH_HOURS);
}

/** Whether the next window should be fetched now: true once the playhead is
 *  within `leadRealSeconds` of real time away from the current window's end,
 *  scaled by playback speed, so a faster speed starts the fetch earlier in
 *  sim-time and still lands before the playhead arrives. */
export function shouldPrefetchNext(sec: number, windowEndSec: number, speed: number, leadRealSeconds = 4): boolean {
  if (speed <= 0) return false;
  const remainingRealSeconds = (windowEndSec - sec) / speed;
  return remainingRealSeconds >= 0 && remainingRealSeconds <= leadRealSeconds;
}

export type WindowKey = { date: string; fromHour: number; toHour: number };

/** The windows the merge needs loaded right now: the one the playhead is in,
 *  the one before it (a trip whose stops straddle the boundary just crossed
 *  still needs it to stay complete), and the next one once the playhead is
 *  close enough to its end to prefetch — at most three, per the "cache a few
 *  windows" budget. */
export function desiredWindowKeys(
  date: string,
  originHour: number,
  playheadSec: number,
  speed: number,
): WindowKey[] {
  const current = windowStartForHour(playheadSec / 3600, originHour);
  const starts = new Set<number>([current]);
  if (current - WINDOW_WIDTH_HOURS >= 0) starts.add(current - WINDOW_WIDTH_HOURS);
  const windowEndSec = (current + WINDOW_WIDTH_HOURS) * 3600;
  if (current + WINDOW_WIDTH_HOURS < SCRUBBER_END_HOUR && shouldPrefetchNext(playheadSec, windowEndSec, speed)) {
    starts.add(current + WINDOW_WIDTH_HOURS);
  }
  return [...starts].sort((a, b) => a - b).map((start) => ({ date, fromHour: start, toHour: start + WINDOW_WIDTH_HOURS }));
}

/** `HH:MM` on the service-day clock, which keeps counting past 24 for a
 *  post-midnight continuation (25:14) rather than wrapping — the same
 *  convention the replay contract's own `t` uses. */
export function formatServiceClock(sec: number): string {
  const hours = Math.floor(sec / 3600);
  const minutes = Math.floor((sec % 3600) / 60);
  return `${String(hours).padStart(2, "0")}:${String(minutes).padStart(2, "0")}`;
}
