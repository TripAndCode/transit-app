import { useEffect, useState } from "react";

/**
 * `true` once the frame after mount has run; `false` on the initial render.
 * Pairs with `ChartEnter.tsx`'s `staggerDelay()` for a grid of many cells
 * (`HourlyHeatmap`, `DowBandGrid`): every cell starts at `opacity: 0` via the
 * shared `.chart-cell-enter` class and needs one "go" signal to add
 * `.chart-cell-enter--in`, rather than each cell scheduling its own frame.
 *
 * ```tsx
 * const entered = useEnteredOnMount();
 * <rect
 *   className={`chart-cell-enter${entered ? " chart-cell-enter--in" : ""}`}
 *   style={{ ...staggerDelay(index), "--cell-opacity": targetOpacity }}
 * />
 * ```
 */
export function useEnteredOnMount(): boolean {
  const [entered, setEntered] = useState(false);
  useEffect(() => {
    const raf = requestAnimationFrame(() => setEntered(true));
    return () => cancelAnimationFrame(raf);
  }, []);
  return entered;
}
