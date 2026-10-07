/**
 * Collapses a burst of calls into one run of `fn` on the next animation
 * frame. For scroll/resize handlers that measure the DOM: a capture-phase
 * scroll listener fires for every scrolling ancestor on every tick, and a
 * synchronous `getBoundingClientRect` in each forces layout as many times.
 * One frame, one measurement.
 *
 * A plain function rather than a hook because the handlers that need it are
 * defined inside effects, where a hook cannot be called. Call `cancel` in the
 * effect's cleanup so an unmounted node is never measured.
 */
export function coalesceToFrame(fn: () => void): { schedule: () => void; cancel: () => void } {
  let frame = 0;
  return {
    schedule() {
      if (frame) return;
      frame = requestAnimationFrame(() => {
        frame = 0;
        fn();
      });
    },
    cancel() {
      if (frame) {
        cancelAnimationFrame(frame);
        frame = 0;
      }
    },
  };
}
