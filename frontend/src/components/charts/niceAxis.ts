/** A value axis over [rawLow, rawHigh], widened to the nearest round step
 *  (1, 2 or 5 × 10ⁿ) so every gridline lands on a number a reader can use.
 *  `intervals` caps how many steps it draws: widening both ends can add a
 *  step, so the smallest round step whose widened range still fits wins. */
export function niceAxis(rawLow: number, rawHigh: number, intervals: number) {
  const magnitude = 10 ** Math.floor(Math.log10((rawHigh - rawLow) / intervals));
  const stepsAt = (step: number) => Math.ceil(rawHigh / step) - Math.floor(rawLow / step);
  const step = ([1, 2, 5, 10, 20].find((multiple) => stepsAt(multiple * magnitude) <= intervals) ?? 50) * magnitude;
  const low = Math.floor(rawLow / step) * step;
  const high = Math.ceil(rawHigh / step) * step;
  const ticks = Array.from({ length: stepsAt(step) + 1 }, (_, index) => low + index * step);
  return { low, high, ticks };
}
