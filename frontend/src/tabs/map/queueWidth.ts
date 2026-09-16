export const MIN_QUEUE_WIDTH = 240;
export const MAX_QUEUE_WIDTH = 720;
export const DEFAULT_QUEUE_WIDTH = 290;
const STORAGE_KEY = "ops.queueWidth";

export const clampQueueWidth = (px: number) => Math.min(MAX_QUEUE_WIDTH, Math.max(MIN_QUEUE_WIDTH, Math.round(px)));

export function readQueueWidth(): number {
  const stored = Number(localStorage.getItem(STORAGE_KEY));
  return Number.isFinite(stored) && stored > 0 ? clampQueueWidth(stored) : DEFAULT_QUEUE_WIDTH;
}

export function storeQueueWidth(px: number) {
  localStorage.setItem(STORAGE_KEY, String(px));
}
