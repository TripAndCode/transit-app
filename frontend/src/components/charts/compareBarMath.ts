export type Period = "weekday" | "weekend";
export const PERIODS: readonly Period[] = ["weekday", "weekend"];

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

/** The shown period's printed figure minus the other's, so the gap always
 *  matches the two figures on screen, has the same size from either side,
 *  and is 0 (never -0) when they print the same. */
export function deltaFor(row: CompareRow, period: Period): number | null {
  const a = row[period];
  const b = row[otherPeriod(period)];
  if (a == null || b == null) return null;
  const tenths = Math.round((Number(a.toFixed(1)) - Number(b.toFixed(1))) * 10);
  return tenths === 0 ? 0 : tenths / 10;
}
