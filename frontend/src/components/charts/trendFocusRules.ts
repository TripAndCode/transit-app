import { DIM_OPACITY, type TrendFocus, type TrendFocusSource } from "./trendFocus";

const SOURCES: readonly TrendFocusSource[] = ["daily", "hourly", "dow"];
const ISO_DATE = /^\d{4}-\d{2}-\d{2}$/;

/** A mark inside a chart other than `source` that carries `dimension` and
 *  disagrees with the focused `value` drops to the dim opacity. The viewer
 *  exclusion rides the chart root's `data-focus-viewer`, because a chart
 *  never dims from its own hover. */
function rule(source: TrendFocusSource, dimension: "dow" | "date", value: string | number, scope: string): string {
  return (
    `.trend-focus[data-focus-source="${source}"]${scope} ` +
    `[data-focus-viewer]:not([data-focus-viewer="${source}"]) ` +
    `[data-mark-${dimension}]:not([data-mark-${dimension}="${value}"]) { --focus-dim: ${DIM_OPACITY}; }`
  );
}

/** Weekday is a closed set, so its rules are fixed: three sources × seven ISO
 *  weekdays. Hour needs no rule — only the hourly chart's marks carry one, and
 *  a chart never dims from its own focus. */
export const STATIC_DIM_RULES: string = SOURCES.flatMap((source) =>
  Array.from({ length: 7 }, (_, i) => rule(source, "dow", i + 1, `[data-focus-dow="${i + 1}"]`)),
).join("\n");

/** The stylesheet the provider emits for `focus`: the static weekday rules,
 *  plus one rule for the focused date when there is one. Dates are open-ended,
 *  so that rule is generated per hover; its value is interpolated into a
 *  selector, so anything but `YYYY-MM-DD` emits nothing. */
export function dimRules(focus: TrendFocus | null): string {
  if (!focus?.date || !ISO_DATE.test(focus.date)) return STATIC_DIM_RULES;
  return `${STATIC_DIM_RULES}\n${rule(focus.source, "date", focus.date, "")}`;
}
