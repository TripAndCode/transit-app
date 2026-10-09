import { useEffect, useState } from "react";
import { prefersReducedMotion } from "../utils/motion";

/**
 * `true` one frame after `hasData` first becomes true, and from then on for
 * the life of the component. The one "go" signal for a grid of many cells
 * (`HourlyHeatmap`, `DowBandGrid`): every cell starts at `opacity: 0` via the
 * shared `.chart-cell-enter` class and flips to `.chart-cell-enter--in`
 * together, staggered by `staggerDelay()`.
 *
 * Keyed on data, not on mount: a chart that mounts before its query resolves
 * would otherwise spend its entrance on an empty grid, and a chart whose data
 * changes in place must not replay it -- the entrance belongs to the first
 * arrival of real content, once.
 *
 * Under `prefers-reduced-motion: reduce` this is `true` from the first render:
 * the CSS enter transition never runs, so waiting a frame would only delay
 * content that renders instantly either way.
 *
 * ```tsx
 * const entered = useFirstData(cells.length > 0);
 * <rect
 *   className={`chart-cell-enter${entered ? " chart-cell-enter--in" : ""}`}
 *   style={{ ...staggerDelay(index), "--cell-opacity": targetOpacity }}
 * />
 * ```
 */
export function useFirstData(hasData: boolean): boolean {
  const [entered, setEntered] = useState(prefersReducedMotion);
  useEffect(() => {
    if (entered || !hasData) return;
    const raf = requestAnimationFrame(() => setEntered(true));
    return () => cancelAnimationFrame(raf);
  }, [entered, hasData]);
  return entered;
}
