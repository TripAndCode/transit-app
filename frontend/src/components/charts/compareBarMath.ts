import { roundTenth } from "../../utils/format";
export type Period = "weekday" | "weekend";
export const PERIODS: readonly Period[] = ["weekday", "weekend"];

export type CompareRow = { route_code: string; weekday: number | null; weekend: number | null };

/** How the list is ranked: by the size of the gap between the two sides,
 *  the report's subject and so the default, or by one side's level. */
export type CompareOrder = "gap" | Period;
export const COMPARE_ORDERS: readonly CompareOrder[] = ["gap", "weekday", "weekend"];

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
 *  the bottom; ties break on route_code ascending so equal figures keep one
 *  order however the rows arrived. */
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
  const tenths = Math.round((roundTenth(a) - roundTenth(b)) * 10);
  return tenths === 0 ? 0 : tenths / 10;
}

/** Largest printed gap first, so the order matches the figures on screen
 *  rather than the unrounded values behind them; a route missing a side has
 *  no gap and sinks to the bottom; ties break on route_code ascending. */
function orderByGap(rows: CompareRow[]): CompareRow[] {
  const size = (r: CompareRow) => {
    const d = deltaFor(r, "weekday");
    return d == null ? null : Math.abs(d);
  };
  return [...rows].sort((a, b) => {
    const av = size(a);
    const bv = size(b);
    if (av == null) return bv == null ? a.route_code.localeCompare(b.route_code) : 1;
    if (bv == null) return -1;
    return bv - av || a.route_code.localeCompare(b.route_code);
  });
}

export function orderRows(rows: CompareRow[], order: CompareOrder): CompareRow[] {
  return order === "gap" ? orderByGap(rows) : orderByPeriod(rows, order);
}

/** The side drawn as the solid bar: the one the list is ranked by, and
 *  平日 while it is ranked by the gap. */
export function solidSide(order: CompareOrder): Period {
  return order === "weekend" ? "weekend" : "weekday";
}

/** Which side runs later and by how much, as printed: `later` is null when
 *  the two print the same. Null when either side has no reading. The verdict
 *  does not depend on which side is shown, so its wording never flips. */
export function gapVerdict(row: CompareRow): { later: Period | null; minutes: number } | null {
  const d = deltaFor(row, "weekday");
  if (d == null) return null;
  if (d === 0) return { later: null, minutes: 0 };
  return { later: d > 0 ? "weekday" : "weekend", minutes: Math.abs(d) };
}
