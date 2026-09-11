import type { FilterCtx } from "../api/types";

/** Format a filter context's date range for display. */
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
