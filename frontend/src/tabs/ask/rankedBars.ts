import { delayRampVar } from "../../styles/tokens";

export type RankedBarKind = "delay" | "accent";

// top_n's three table shapes (pipeline/query/tools.py's _tool_top_n) are the
// only `kind: "table"` answers that rank routes by one metric: a day/service
// breakdown, a weekday-vs-weekend comparison and a stop profile ordered by
// sequence all share the table renderer but are not a single-metric ranking,
// so they get no bar.
const RANKED_METRIC_COLUMNS: Record<string, RankedBarKind> = {
  avg_min: "delay",
  on_time_pct: "accent",
  late5_count: "accent",
};

/** The column a ranked-routes table draws its bars against, if any: always
 *  index 1, right after `route_code`. */
export function rankedBarColumn(columns: readonly string[]): { index: number; kind: RankedBarKind } | null {
  if (columns[0] !== "route_code") return null;
  const kind = RANKED_METRIC_COLUMNS[columns[1]];
  return kind ? { index: 1, kind } : null;
}

/** Each row's bar share (0-1) of the largest value shown, for the column
 *  `rankedBarColumn` names. A non-finite, missing or non-positive value gets
 *  no bar (`null`) rather than a negative or `NaN` width; when every shown
 *  value is non-positive, every row gets no bar, since there is nothing to
 *  be the largest of. */
export function rankedBarShares(values: readonly unknown[]): (number | null)[] {
  const finite = values.filter((v): v is number => typeof v === "number" && Number.isFinite(v) && v > 0);
  const max = finite.length > 0 ? Math.max(...finite) : 0;
  if (max <= 0) return values.map(() => null);
  return values.map((v) => (typeof v === "number" && Number.isFinite(v) && v > 0 ? v / max : null));
}

/** The bar's fill colour: the delay ramp for a delay-minutes metric (never
 *  alarm red -- see styles/tokens.ts), the product accent for a metric where
 *  a bigger bar is not necessarily worse (an on-time rate, a late-trip
 *  count). */
export function rankedBarColor(kind: RankedBarKind, value: number): string {
  return kind === "delay" ? delayRampVar(value) : "var(--accent)";
}
