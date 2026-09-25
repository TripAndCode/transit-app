/** Arithmetic mean of a sparkline's series -- the value a caller passes back
 *  to `InlineSparkline`'s `baseline` to anchor an otherwise unlabelled
 *  auto-scaled series against the period's own normal.
 *
 *  Null below two points, which is the same threshold `InlineSparkline`
 *  refuses to draw at. A single reading has a mean, but a reference line
 *  through the only value it could be compared against says nothing, and a
 *  caller keying its legend on this would caption a chart that never
 *  rendered.
 *
 *  Its own module rather than a second export from `InlineSparkline.tsx`: a
 *  file that exports both a component and a plain function loses fast
 *  refresh for the component. */
export function periodMean(points: number[]): number | null {
  if (points.length < 2) return null;
  return points.reduce((total, v) => total + v, 0) / points.length;
}
