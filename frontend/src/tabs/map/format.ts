import type { TFunction } from "i18next";

/** Formats a trip's scheduled HH:MM:SS time as HH:MM, or a placeholder when absent. */
export function hhmm(trip: { scheduled_time: string | null }): string {
  return trip.scheduled_time?.slice(0, 5) ?? "--:--";
}

/** How long a feed has been quiet, in the coarsest unit that still says it:
 *  minutes within the hour, hours within the day, then days. */
export function quietFor(ageMs: number, t: TFunction): string {
  const minutes = Math.floor(ageMs / 60_000);
  if (minutes < 60) return t("operations.quiet_for.minutes", { n: minutes });
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return t("operations.quiet_for.hours", { n: hours });
  return t("operations.quiet_for.days", { count: Math.floor(hours / 24) });
}
