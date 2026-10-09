import { useEffect, useRef, useState } from "react";
import { useMediaQuery } from "./useMediaQuery";

type UseCountUpOptions = {
  /** Animation length in ms. 0 (or less) jumps straight to `value`. */
  duration?: number;
  /** Decimal places the intermediate displayed value is rounded to. */
  decimals?: number;
};

// 1 - (1-t)^3 -- decelerates hard, matching --ease-out's cubic-bezier(.22, 1,
// .36, 1) closely enough for a JS-driven numeric tween (the CSS easing curve
// itself only applies to animated CSS properties, not to a number a
// component re-renders with).
function easeOutCubic(t: number): number {
  return 1 - Math.pow(1 - t, 3);
}

function round(n: number, decimals: number): number {
  const factor = 10 ** decimals;
  return Math.round(n * factor) / factor;
}

/**
 * Animates a displayed number toward `value` with an ease-out rAF loop, for a
 * large figure (a KPI hero value, a stat tile, a per-row delay figure).
 *
 * The first paint prints `value` as it is: a route has exactly one entrance
 * (the route fade), and a figure climbing from 0 inside it would be a second
 * arrival stacked on the first. Later changes to `value` -- an agency switch,
 * a filter change, a live refresh -- travel from whatever is on screen.
 *
 * Jumps straight to `value` (no rAF loop at all) under
 * `prefers-reduced-motion: reduce`, and whenever `duration` is 0.
 *
 * Formatting -- decimal places, locale grouping, a unit suffix -- is the
 * caller's job; this hook only ever returns a plain number. Prefer
 * `n.toLocaleString(i18n.language)` over a bare `toLocaleString()` at the
 * call site so digit grouping follows the active UI language.
 */
export function useCountUp(value: number, { duration = 600, decimals = 1 }: UseCountUpOptions = {}): number {
  const reducedMotion = useMediaQuery("(prefers-reduced-motion: reduce)");
  const immediate = duration <= 0 || reducedMotion;

  const [display, setDisplay] = useState(value);
  const fromRef = useRef(value);

  // Re-sync during render: in immediate mode `display` must equal `value`, so
  // comparing the two also catches motion being switched off mid-tween, which
  // would otherwise strand the figure on its last frame.
  if (immediate && display !== value) {
    setDisplay(value);
  }

  useEffect(() => {
    if (immediate) {
      fromRef.current = value;
      return;
    }
    const from = fromRef.current;
    const delta = value - from;
    if (delta === 0) return;

    let frameId = 0;
    let startTime: number | null = null;

    function tick(now: number) {
      if (startTime === null) startTime = now;
      const t = Math.min(1, (now - startTime) / duration);
      // Written every frame, not only on arrival: a `value` that changes
      // mid-tween cancels this loop, and the next one starts from wherever
      // the figure actually is.
      fromRef.current = round(from + delta * easeOutCubic(t), decimals);
      setDisplay(fromRef.current);
      if (t < 1) frameId = requestAnimationFrame(tick);
    }

    frameId = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(frameId);
  }, [value, duration, decimals, immediate]);

  return display;
}
