import { useEffect, useEffectEvent } from "react";

/**
 * A throttled rAF loop shared by the map's long-running paint animations
 * (the route's flow dash and the reported-segment pearl). Repainting on every
 * vsync would rewrite a paint property ~60 times a second to advance a cycle
 * lasting seconds, so writes are gated to `intervalMs` -- still far finer
 * than the eye resolves against those cycles. `onTick` receives elapsed ms
 * since the loop started; it is read through useEffectEvent so a changed
 * closure never restarts the clock.
 */
export function useFlowTick(active: boolean, intervalMs: number, onTick: (elapsedMs: number) => void): void {
  const tick = useEffectEvent(onTick);
  useEffect(() => {
    if (!active) return;
    let frameId = 0;
    const start = performance.now();
    let lastPaint = -Infinity;
    function frame(now: number) {
      frameId = requestAnimationFrame(frame);
      if (now - lastPaint < intervalMs) return;
      lastPaint = now;
      tick(now - start);
    }
    frameId = requestAnimationFrame(frame);
    return () => cancelAnimationFrame(frameId);
  }, [active, intervalMs]);
}
