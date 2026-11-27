import type { LiveTrip } from "../api/types";

// Two beyond a minute early, one on the early edge, two inside the late side,
// one on the 5-minute edge and one past it. The industry default counts 4 of
// 7 on time and any-early-to-1-min counts 3 of 7, so a rate shows which
// window it counted.
export const WINDOW_DELAYS = [-90, -61, -60, 120, 299, 300, 301];

/** One freshly reported trip per delay, in seconds. */
export function liveRowsWithDelays(delays: number[]): LiveTrip[] {
  const capturedAt = new Date().toISOString();
  return delays.map((dep_delay, i) => ({
    trip_id: `t${i}`,
    route_code: "R1",
    service_type: null,
    scheduled_time: "08:00:00",
    dep_delay,
    captured_at: capturedAt,
    stop_id: `s${i}`,
    stop_sequence: 1,
    stop_name: "Stop",
    stop_lat: 40.8,
    stop_lon: 140.7,
    headsign: "Downtown",
  }));
}
