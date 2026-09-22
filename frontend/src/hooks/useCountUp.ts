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
 * large standalone figure (a KPI hero value, a stat tile, a per-row delay
 * figure) whose target changes after mount -- an agency switch, a filter
 * change, a live refresh. The first render never animates: there is nothing
 * to count up *from* yet, so it returns `value` immediately. Later changes to
 * `value` animate from whatever is currently displayed.
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
  // The last `value` `display` was synced to while in "immediate" mode.
  // Comparing the incoming `value` against this (rather than against
  // `display` itself, which drifts away from `value` mid-animation) is the
  // standard adjust-state-when-a-prop-changes pattern: it lets the branch
  // below re-sync during render, with no effect involved. A plain ref
  // couldn't stand in for it -- a ref may only be written inside an effect
  // or callback, never during render.
  const [immediateTarget, setImmediateTarget] = useState(value);
  const fromRef = useRef(value);

  if (immediate && value !== immediateTarget) {
    setImmediateTarget(value);
    setDisplay(value);
  }

  useEffect(() => {
    if (immediate) {
      fromRef.current = value;
      return;
    }
    const from = fromRef.current;
    const delta = value - from;
    if (delta === 0) {
      return;
    }

    let frameId = 0;
    let startTime: number | null = null;

    function tick(now: number) {
      if (startTime === null) startTime = now;
      const elapsed = now - startTime;
      const t = Math.min(1, elapsed / duration);
      setDisplay(round(from + delta * easeOutCubic(t), decimals));
      if (t < 1) {
        frameId = requestAnimationFrame(tick);
      } else {
        fromRef.current = value;
      }
    }

    frameId = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(frameId);
  }, [value, duration, decimals, immediate]);

  return display;
}
