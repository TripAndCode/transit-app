import { delayRampVar } from "../../styles/tokens";

export type RankedBarKind = "delay" | "accent";

// A table gets a ranked bar exactly when its columns start with `route_code`
// followed by one of RANKED_METRIC_COLUMNS below -- that shape is how
// pipeline/query/tools.py's _tool_top_n (all three metrics) and
// _tool_on_time_rate mark a single-metric ranking of routes. Other tables the
// same renderer draws (day/service breakdowns, weekday-vs-weekend, stop
// profiles) don't match the shape, so they get no bar.
const RANKED_METRIC_COLUMNS: Record<string, RankedBarKind> = {
  avg_min: "delay",
  on_time_pct: "accent",
  late5_count: "accent",
};

/** Rows past this index share its entrance delay, so even a long table's
 *  last bar starts growing a few `--dur-1` steps in rather than seconds later. */
export const MAX_STAGGER = 8;

/** The column a ranked-routes table draws its bars against, if any: always
 *  index 1, right after `route_code`. */
export function rankedBarColumn(columns: readonly string[]): { index: number; kind: RankedBarKind } | null {
  if (columns[0] !== "route_code") return null;
  const kind = RANKED_METRIC_COLUMNS[columns[1]];
  return kind ? { index: 1, kind } : null;
}

/** Each row's bar share (0-1) of the largest value shown, for the column
 *  `rankedBarColumn` names: one share per row, so every row keeps its track
 *  and stays aligned. A non-finite, missing or non-positive value gets a
 *  share of 0 (an empty track) rather than a negative or `NaN` width; when
 *  every shown value is non-positive, every row's share is 0, since there is
 *  nothing to be the largest of. */
export function rankedBarShares(values: readonly unknown[]): number[] {
  const finite = values.filter((v): v is number => typeof v === "number" && Number.isFinite(v) && v > 0);
  const max = finite.length > 0 ? Math.max(...finite) : 0;
  if (max <= 0) return values.map(() => 0);
  return values.map((v) => (typeof v === "number" && Number.isFinite(v) && v > 0 ? v / max : 0));
}

/** The bar's fill colour: the delay ramp for a delay-minutes metric (never
 *  alarm red -- see styles/tokens.ts), the product accent for everything
 *  else. `delayRampVar` is keyed on minutes, so only `avg_min` belongs on it;
 *  a percentage (on-time rate) or a count (late trips) has no minutes scale
 *  to map onto that ramp and gets the flat accent instead. `value` falls back
 *  to 0 for a non-finite or missing cell, which is always paired with a 0
 *  share, so the colour never shows. */
export function rankedBarColor(kind: RankedBarKind, value: unknown): string {
  const v = typeof value === "number" && Number.isFinite(value) ? value : 0;
  return kind === "delay" ? delayRampVar(v) : "var(--accent)";
}
