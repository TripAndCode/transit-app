import type { FilterCtx } from "../api/types";
import { formatDateRange } from "./format";

/**
 * Format a filter context's date range for display.
 *
 * A preset name ("Last 30 days") promises recency, so it is used only when the
 * window ends on `anchorDay`, the day the period presets end on (the agency's
 * latest data day, see `defaultPeriod`). Any other window, such as a past
 * calendar month or a thread saved under an older anchor, is written out as
 * explicit dates.
 */
export function rangeLabel(
  fc: FilterCtx,
  t: (key: string, opts?: Record<string, unknown>) => string,
  anchorDay: string,
): string | null {
  if (!fc.from_date || !fc.to_date) return null;
  if (fc.to_date === anchorDay) {
    const from = new Date(fc.from_date);
    const to = new Date(fc.to_date);
    const days = Math.round((to.getTime() - from.getTime()) / (24 * 60 * 60 * 1000));
    if (days === 6 || days === 7) return t("filters.range.last_7d");
    if (days >= 28 && days <= 31) return t("filters.range.last_30d");
    if (days >= 85 && days <= 92) return t("filters.range.last_90d");
  }
  return formatDateRange(fc.from_date, fc.to_date);
}
