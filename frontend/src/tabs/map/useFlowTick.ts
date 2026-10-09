import { useEffect, useEffectEvent } from "react";

/**
 * A throttled rAF loop for the map's long-running paint animations (the
 * route's flow dash and the reported-segment pearl each run one). Repainting
 * on every vsync would rewrite a paint property ~60 times a second to advance
 * a cycle lasting seconds, so writes are gated to `intervalMs` -- still far
 * finer than the eye resolves against those cycles. The gate is a grid of
 * frame time shared by every loop, so loops started apart write in the same
 * frame and the map redraws once for all of them. `onTick` receives elapsed
 * ms since the loop's first frame; it is read through useEffectEvent so a
 * changed closure never restarts the clock.
 */
export function useFlowTick(active: boolean, intervalMs: number, onTick: (elapsedMs: number) => void): void {
  const tick = useEffectEvent(onTick);
  useEffect(() => {
    if (!active) return;
    let frameId = 0;
    // Anchored on the first frame's own stamp: a frame stamp can precede a
    // performance.now() read taken before it, which would start below zero.
    let start: number | null = null;
    let lastSlot: number | null = null;
    function frame(now: number) {
      frameId = requestAnimationFrame(frame);
      const slot = Math.floor(now / intervalMs);
      if (slot === lastSlot) return;
      lastSlot = slot;
      if (start == null) start = now;
      tick(now - start);
    }
    frameId = requestAnimationFrame(frame);
    return () => cancelAnimationFrame(frameId);
  }, [active, intervalMs]);
}
