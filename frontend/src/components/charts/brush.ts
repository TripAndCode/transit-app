/** The date range a completed brush selects, in the range context's format. */
type BrushSelection = { from: string; to: string };

function clampIndex(value: number, last: number): number {
  return Math.min(last, Math.max(0, Math.round(value)));
}

/**
 * The inclusive `[from, to]` dates a drag from `startIdx` to `endIdx` covers.
 *
 * Both bounds are clamped into the series and ordered, so a right-to-left
 * drag and a drag that ran off the edge of the plot produce the same range a
 * reader would draw by hand. `null` for an empty series or a non-finite
 * bound — the caller has nothing to select, rather than an index to guess at.
 */
export function brushRange(
  startIdx: number,
  endIdx: number,
  days: { date: string }[],
): BrushSelection | null {
  if (days.length === 0) return null;
  if (!Number.isFinite(startIdx) || !Number.isFinite(endIdx)) return null;
  const last = days.length - 1;
  const a = clampIndex(startIdx, last);
  const b = clampIndex(endIdx, last);
  return { from: days[Math.min(a, b)].date, to: days[Math.max(a, b)].date };
}

/** Every index a drag covers, ascending — what the in-progress shading and
 *  the keyboard selection both iterate. */
export function brushIndices(startIdx: number, endIdx: number): number[] {
  const lo = Math.min(startIdx, endIdx);
  const hi = Math.max(startIdx, endIdx);
  const out: number[] = [];
  for (let i = lo; i <= hi; i++) out.push(i);
  return out;
}
