/** Arithmetic mean of a sparkline's series, or null for an empty one -- the
 *  value a caller passes back to `InlineSparkline`'s `baseline` to anchor an
 *  otherwise unlabelled auto-scaled series against the period's own normal.
 *
 *  Its own module rather than a second export from `InlineSparkline.tsx`: a
 *  file that exports both a component and a plain function loses fast
 *  refresh for the component. */
export function periodMean(points: number[]): number | null {
  if (points.length === 0) return null;
  return points.reduce((total, v) => total + v, 0) / points.length;
}
