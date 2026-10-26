import { MAX_REPORT_AGE_MS } from "./liveRowsFilter";

/** Why the live map has no vehicle to draw:
 *  - `none`: no report has ever arrived, live or in the history;
 *  - `overnight`: it is the overnight break and the feed last reported during
 *    the service day that the night closes;
 *  - `stale`: the feed has sent nothing for longer than the live window;
 *  - `quiet`: the feed is current, so nothing is reporting on the routes shown. */
export type EmptyMapReason = "none" | "overnight" | "stale" | "quiet";

const HOUR_MS = 3_600_000;
const DAY_MS = 24 * HOUR_MS;
/** JST has no daylight saving, so a fixed offset is exact. */
const JST_OFFSET_MS = 9 * HOUR_MS;
/** The service day turns over at 04:00 JST: pipeline/service_day.py's
 *  SERVICE_DAY_CUTOVER_HOURS. */
const SERVICE_DAY_CUTOVER_MS = 4 * HOUR_MS;
/** The overnight break is 00:00 to 05:00 JST: the app's 深夜 time band, and
 *  the hour the day playback's service window opens. A few after-midnight
 *  trips report inside it; none report between 02:00 and 05:00. */
const OVERNIGHT_END_MS = 5 * HOUR_MS;

function jstMidnight(ms: number): number {
  return Math.floor((ms + JST_OFFSET_MS) / DAY_MS) * DAY_MS - JST_OFFSET_MS;
}

/** Whether the night accounts for silence at `clock`. Between 04:00 and 05:00
 *  the new service day has begun but nothing departs yet, so the night closes
 *  the service day that began at 04:00 the previous calendar day, and a last
 *  report from before then is a feed that stopped earlier. */
function overnightSilence(lastReportMs: number, clock: number): boolean {
  const midnight = jstMidnight(clock);
  if (clock >= midnight + OVERNIGHT_END_MS) return false;
  return lastReportMs >= midnight - DAY_MS + SERVICE_DAY_CUTOVER_MS;
}

/** Reads `clock` rather than the wall clock, so a render stays pure and the
 *  live map's own tick moves the answer across 05:00. */
export function emptyMapReason(lastReport: string | null, clock: number): EmptyMapReason {
  if (lastReport == null) return "none";
  const lastReportMs = Date.parse(lastReport);
  if (!Number.isFinite(lastReportMs)) return "quiet";
  if (overnightSilence(lastReportMs, clock)) return "overnight";
  return clock - lastReportMs > MAX_REPORT_AGE_MS ? "stale" : "quiet";
}
