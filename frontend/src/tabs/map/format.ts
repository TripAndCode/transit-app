/** Formats a trip's scheduled HH:MM:SS time as HH:MM, or a placeholder when absent. */
export function hhmm(trip: { scheduled_time: string | null }): string {
  return trip.scheduled_time?.slice(0, 5) ?? "--:--";
}
