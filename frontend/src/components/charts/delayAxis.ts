/** The spans a delay bar may be drawn against, in minutes. A bar list picks
 *  one step for everything it can show, so every bar in it is measured
 *  against the same span and a toggle between views of the same list (one
 *  side of a comparison or the other) moves the bars, never the axis. Steps
 *  rather than the exact maximum keep the axis a round, readable figure that
 *  changes only when the data crosses a step. A delay past the last step
 *  fills the bar and keeps its exact figure beside it. */
const DELAY_AXIS_STEPS_MIN = [6, 12, 18, 24] as const;

/** The smallest step that holds every figure; the first step when none is
 *  above zero. */
export function delayAxisMax(values: readonly (number | null)[]): number {
  let largest = 0;
  for (const v of values) if (v != null && v > largest) largest = v;
  return DELAY_AXIS_STEPS_MIN.find((step) => largest <= step) ?? DELAY_AXIS_STEPS_MIN[DELAY_AXIS_STEPS_MIN.length - 1];
}

/** A delay's share of the axis, clamped to [0, 1]; no reading is 0. */
export function delayAxisShare(min: number | null, axisMax: number): number {
  if (min == null) return 0;
  return Math.min(1, Math.max(0, min / axisMax));
}
