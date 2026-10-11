export const MIN_QUEUE_WIDTH = 240;
export const MAX_QUEUE_WIDTH = 720;
/* Wide enough for this panel's real content to hold one line: a route name
   plus its pattern id, and the trip panel's stop names. At 290 each of those
   wrapped to three lines, which reads as a broken column rather than a narrow
   one. Still resizable from the seam, and the stored width wins. */
export const DEFAULT_QUEUE_WIDTH = 380;
const STORAGE_KEY = "ops.queueWidth";

export const clampQueueWidth = (px: number) => Math.min(MAX_QUEUE_WIDTH, Math.max(MIN_QUEUE_WIDTH, Math.round(px)));

/* Storage can be absent or refuse access (blocked site data, private modes),
   and a width preference is never worth failing the Live tab's render for: a
   read falls back to the default and a write is dropped. */
export function readQueueWidth(): number {
  try {
    const stored = Number(localStorage.getItem(STORAGE_KEY));
    if (Number.isFinite(stored) && stored > 0) return clampQueueWidth(stored);
  } catch {
    /* storage unavailable: use the default */
  }
  return DEFAULT_QUEUE_WIDTH;
}

export function storeQueueWidth(px: number) {
  try {
    localStorage.setItem(STORAGE_KEY, String(px));
  } catch {
    /* storage unavailable: the width lasts for this session only */
  }
}
