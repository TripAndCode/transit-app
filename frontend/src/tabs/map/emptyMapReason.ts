import type { ServiceHoursResponse } from "../../api/types";
import { CLOCK_SKEW_ALLOWANCE_MS } from "../../utils/clockSkew";
import { MAX_REPORT_AGE_MS } from "./liveRowsFilter";

/** Why the live map has no vehicle to draw:
 *  - `none`: no report has ever arrived, live or in the history;
 *  - `before_first`: by the timetable, the service day's first trip has not
 *    left yet, and the feed last reported during the service day before;
 *  - `ended`: by the timetable, the service day's last trip has arrived, and
 *    the feed last reported during that service day;
 *  - `overnight`: with no timetable hours for the day, it is the overnight
 *    break and the feed last reported during the service day the night closes;
 *  - `stale`: the feed has sent nothing for longer than the live window while
 *    trips were due;
 *  - `quiet`: the feed is current, so nothing is reporting on the routes shown,
 *    or its timestamp is unreadable or ahead of the clock, so neither the
 *    timetable nor a stall can be claimed. */
export type EmptyMapReason = "none" | "before_first" | "ended" | "overnight" | "stale" | "quiet";

const HOUR_MS = 3_600_000;
const DAY_MS = 24 * HOUR_MS;
/** JST has no daylight saving, so a fixed offset is exact. */
const JST_OFFSET_MS = 9 * HOUR_MS;
/** The service day turns over at 04:00 JST: pipeline/service_day.py's
 *  SERVICE_DAY_CUTOVER_HOURS. */
const SERVICE_DAY_CUTOVER_MS = 4 * HOUR_MS;
/** Without timetable hours, the overnight break is 00:00 to 05:00 JST: the
 *  app's 深夜 time band, and the hour the day playback's service window opens. */
const OVERNIGHT_END_MS = 5 * HOUR_MS;

function jstMidnight(ms: number): number {
  return Math.floor((ms + JST_OFFSET_MS) / DAY_MS) * DAY_MS - JST_OFFSET_MS;
}

/** When the service day `ms` falls in began: 04:00 JST of its date. */
function serviceDayStart(ms: number): number {
  return jstMidnight(ms - SERVICE_DAY_CUTOVER_MS) + SERVICE_DAY_CUTOVER_MS;
}

/** The JST service date `ms` falls in, as YYYY-MM-DD, by the rule
 *  pipeline/service_day.py's service_date_of applies. */
export function serviceDateOf(ms: number): string {
  return new Date(ms + JST_OFFSET_MS - SERVICE_DAY_CUTOVER_MS).toISOString().slice(0, 10);
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

/** The hours that apply at `clock`: the timetable's, when they are for the
 *  service day `clock` is in and both instants read. */
function hoursAt(clock: number, today: ServiceHoursResponse | null | undefined): { first: number; last: number } | null {
  if (!today?.hours || today.date !== serviceDateOf(clock)) return null;
  const first = Date.parse(today.hours.first_departure_at);
  const last = Date.parse(today.hours.last_arrival_at);
  return Number.isFinite(first) && Number.isFinite(last) ? { first, last } : null;
}

/** The reason for a feed quiet past the live window, by the timetable's hours.
 *  Outside them, the timetable accounts for the silence only if the feed last
 *  reported during the service it follows: the day ending, or before the
 *  first trip, the day before. Inside them, the silence counts from the first
 *  departure at the earliest, so the first trip has the live window to start
 *  reporting. */
function timetableReason(lastReportMs: number, clock: number, hours: { first: number; last: number }): EmptyMapReason {
  const dayStart = serviceDayStart(clock);
  if (clock < hours.first) return lastReportMs >= dayStart - DAY_MS ? "before_first" : "stale";
  if (clock > hours.last) return lastReportMs >= dayStart ? "ended" : "stale";
  return clock - Math.max(lastReportMs, hours.first) > MAX_REPORT_AGE_MS ? "stale" : "quiet";
}

/** Reads `clock` rather than the wall clock, so a render stays pure and the
 *  live map's own tick moves the answer across the hours' edges. `today` is
 *  the timetable's hours; without hours for the service day `clock` is in, the
 *  fixed overnight break stands in for them. */
export function emptyMapReason(
  lastReport: string | null,
  clock: number,
  today?: ServiceHoursResponse | null,
): EmptyMapReason {
  if (lastReport == null) return "none";
  const lastReportMs = Date.parse(lastReport);
  const age = clock - lastReportMs;
  // A report further ahead of the clock than the skew allowance can't date the
  // silence, the same rule MapTab's freshness badge and filterLiveRows apply.
  if (!Number.isFinite(age) || age < CLOCK_SKEW_ALLOWANCE_MS) return "quiet";
  const hours = hoursAt(clock, today);
  if (hours) return age > MAX_REPORT_AGE_MS ? timetableReason(lastReportMs, clock, hours) : "quiet";
  if (overnightSilence(lastReportMs, clock)) return "overnight";
  return age > MAX_REPORT_AGE_MS ? "stale" : "quiet";
}
