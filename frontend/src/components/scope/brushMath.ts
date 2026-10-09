import { isoDaysBefore } from "../../api/scope";

/** The API's longest allowed range (api/range.py MAX_RANGE_DAYS). */
const MAX_RANGE_DAYS = 365;

/** The day under an x offset into a strip of `count` equal-width days. */
export function dayIndexAt(x: number, width: number, count: number): number {
  if (count <= 0 || width <= 0) return 0;
  return Math.min(count - 1, Math.max(0, Math.floor((x / width) * count)));
}

/** A drag's two ends as a start-first range. */
export function rangeFrom(a: number, b: number): [number, number] {
  return a <= b ? [a, b] : [b, a];
}

/** The first day of "since collection began": the earliest data day, or as
 *  far back as the API allows a range to reach from `latest`. */
export function sinceStart(earliest: string, latest: string): string {
  const floor = isoDaysBefore(latest, MAX_RANGE_DAYS - 1);
  return earliest > floor ? earliest : floor;
}

/** Every calendar day from `from` to `to` inclusive, days without data
 *  included, so a gap shows as a gap. */
export function calendarDays(from: string, to: string): string[] {
  const days: string[] = [];
  const d = new Date(`${from}T00:00:00Z`);
  const end = new Date(`${to}T00:00:00Z`);
  while (d <= end) {
    days.push(d.toISOString().slice(0, 10));
    d.setUTCDate(d.getUTCDate() + 1);
  }
  return days;
}
