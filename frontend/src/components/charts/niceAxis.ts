const STEP_MULTIPLES = [1, 2, 5, 10, 20, 50];

/** A value axis over [rawLow, rawHigh], widened to the nearest round step
 *  (1, 2 or 5 × 10ⁿ) so every gridline lands on a number a reader can use.
 *  `intervals` caps how many steps it draws: widening both ends can add a
 *  step, so the smallest round step whose widened range still fits wins. */
export function niceAxis(rawLow: number, rawHigh: number, intervals: number) {
  const magnitude = 10 ** Math.floor(Math.log10((rawHigh - rawLow) / intervals));
  let axis = { low: rawLow, high: rawHigh, ticks: [rawLow, rawHigh] };
  for (const multiple of STEP_MULTIPLES) {
    const step = multiple * magnitude;
    const low = Math.floor(rawLow / step) * step;
    const high = Math.ceil(rawHigh / step) * step;
    const count = Math.round((high - low) / step);
    axis = { low, high, ticks: Array.from({ length: count + 1 }, (_, index) => low + index * step) };
    if (count <= intervals) break;
  }
  return axis;
}
