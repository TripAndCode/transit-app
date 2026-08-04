import type { FilterCtx } from "../api/types";

/** Humanize a filter context's date range for display, or null if it has no
 *  range set (callers decide their own fallback — ThreadSidebar shows
 *  nothing, FilterContextBar defaults to "last 30 days"). Recognizes the
 *  common 7/30/90-day presets by name; anything else renders as the literal
 *  from/to dates joined by the locale-aware separator.
 *
 *  Extracted because this exact block was independently duplicated in
 *  ThreadSidebar.tsx and FilterContextBar.tsx, and the same hardcoded
 *  separator bug had to be fixed in both places — a single shared
 *  implementation is what actually prevents a third recurrence. */
export function rangeLabel(fc: FilterCtx, t: (key: string, opts?: Record<string, unknown>) => string): string | null {
  if (!fc.from_date || !fc.to_date) return null;
  const from = new Date(fc.from_date);
  const to = new Date(fc.to_date);
  const days = Math.round((to.getTime() - from.getTime()) / (24 * 60 * 60 * 1000));
  if (days === 6 || days === 7) return t("filters.range.last_7d");
  if (days >= 28 && days <= 31) return t("filters.range.last_30d");
  if (days >= 85 && days <= 92) return t("filters.range.last_90d");
  return `${fc.from_date} ${t("common.range_separator")} ${fc.to_date}`;
}
