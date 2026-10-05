/** A value axis over [rawLow, rawHigh], widened to the nearest round step
 *  (1, 2 or 5 × 10ⁿ) so every gridline lands on a number a reader can use.
 *  `intervals` caps how many steps it draws. */
export function niceAxis(rawLow: number, rawHigh: number, intervals: number) {
  const roughStep = (rawHigh - rawLow) / intervals;
  const magnitude = 10 ** Math.floor(Math.log10(roughStep));
  const step = ([1, 2, 5, 10].find((value) => value * magnitude >= roughStep) ?? 10) * magnitude;
  const low = Math.floor(rawLow / step) * step;
  const high = Math.ceil(rawHigh / step) * step;
  const ticks = Array.from({ length: Math.round((high - low) / step) + 1 }, (_, index) => low + index * step);
  return { low, high, ticks };
}
