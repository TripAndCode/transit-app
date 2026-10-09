/** The span every delay bar is drawn against, never the current maximum: a
 *  bar whose axis moves with the data says nothing about how one bar compares
 *  to another, or to the same one last week. A delay past the top fills the
 *  bar and keeps its exact figure beside it. */
export const DELAY_AXIS_MAX_MIN = 6;

/** A delay's share of the fixed axis, clamped to [0, 1]; no reading is 0. */
export function delayAxisShare(min: number | null): number {
  if (min == null) return 0;
  return Math.min(1, Math.max(0, min / DELAY_AXIS_MAX_MIN));
}
