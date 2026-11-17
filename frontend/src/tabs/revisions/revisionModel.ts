import type { RevisionDayType, TimetableRevision } from "../../api/types";
import { EM_DASH } from "../../utils/format";

/** The day types a revision is compared on, in display order. */
export const REVISION_DAY_TYPES = ["weekday", "saturday", "sunday_holiday"] as const satisfies readonly RevisionDayType[];

/** A departure in seconds after the service day's midnight, written as the
 *  timetable writes it: a departure after midnight reads 24:30, not 0:30. */
export function serviceClock(sec: number | null): string {
  if (sec == null) return EM_DASH;
  const hours = Math.floor(sec / 3600);
  const minutes = Math.floor((sec % 3600) / 60);
  return `${hours}:${String(minutes).padStart(2, "0")}`;
}

/** The revision a page shows: the one `on` names when the list has it, else
 *  the newest, which the list gives first. */
export function pickRevision(revisions: readonly TimetableRevision[], on: string | null): TimetableRevision | null {
  return revisions.find((r) => r.first_day === on) ?? revisions[0] ?? null;
}
