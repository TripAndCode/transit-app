export type Period = "weekday" | "weekend";
export const PERIODS: readonly Period[] = ["weekday", "weekend"];
/** Fixed axis, never the current maximum: a bar whose axis moves with the
 *  data says nothing about how one period compares to the other. */
export const COMPARE_AXIS_MAX_MIN = 6;

export type CompareRow = { route_code: string; weekday: number | null; weekend: number | null };

function cell(v: unknown): number | null {
  if (v == null || v === "") return null;
  const n = Number(v);
  return Number.isFinite(n) ? n : null;
}

/** `compare_ranking` rows are `[route_code, weekday_avg_min, weekend_avg_min,
 *  abs_delta_min, signed_delta_min]`; the two deltas are recomputed here per
 *  shown period, so only the first three columns are read. */
export function parseCompareRows(rows: readonly unknown[][]): CompareRow[] {
  return rows.map((r) => ({ route_code: String(r[0] ?? ""), weekday: cell(r[1]), weekend: cell(r[2]) }));
}

export function otherPeriod(p: Period): Period {
  return p === "weekday" ? "weekend" : "weekday";
}

/** Worst first by the shown period; a route with no reading in it sinks to
 *  the bottom; ties break on route_code ascending because the API's order
 *  (by absolute delta) is not the one a per-period bar chart reads in. */
export function orderByPeriod(rows: CompareRow[], period: Period): CompareRow[] {
  return [...rows].sort((a, b) => {
    const av = a[period];
    const bv = b[period];
    if (av == null) return bv == null ? a.route_code.localeCompare(b.route_code) : 1;
    if (bv == null) return -1;
    return bv - av || a.route_code.localeCompare(b.route_code);
  });
}

export function barScale(value: number | null, axisMax: number = COMPARE_AXIS_MAX_MIN): number {
  if (value == null) return 0;
  return Math.min(1, Math.max(0, value / axisMax));
}

export function deltaFor(row: CompareRow, period: Period): number | null {
  const a = row[period];
  const b = row[otherPeriod(period)];
  return a == null || b == null ? null : a - b;
}
